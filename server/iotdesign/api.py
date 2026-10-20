import asyncio
import hashlib
import html
import io
import json
import logging
import re
import secrets
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from zipfile import ZipFile

import av
import qrcode
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from PIL import Image, UnidentifiedImageError

from .config import Settings
from .db import Database, now
from .discovery import Advertisement
from .live import LivePreviews
from .models import (
    FrameResponse,
    Health,
    JobResponse,
    ObjectReviewRequest,
    PairRequest,
    PairResponse,
    ReconstructRequest,
    ScanRequest,
    ScanResponse,
    SceneResponse,
    TrackingBatch,
    TrackingSample,
)
from .security import Pairing, ensure_certificate, lan_addresses, token_hash
from .streaming import STREAM_FRAME_LIMIT, schedule_snapshot
from .video import VideoReceiver

log = logging.getLogger(__name__)
ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def create_app(settings: Settings | None = None):
    previews = LivePreviews()
    review_lock = threading.Lock()
    video_sessions = set()
    settings = settings or Settings()
    db = Database(settings.db_path)
    server_id = db.get_meta("server_id", lambda: str(uuid.uuid4()))
    _, _, fingerprint = ensure_certificate(settings.data)
    pairing = Pairing()

    @asynccontextmanager
    async def lifespan(app):
        advertisement = None
        if settings.advertise:
            try:
                advertisement = await asyncio.to_thread(Advertisement, settings.name, server_id,
                                                        settings.port, fingerprint)
            except Exception:
                log.exception("mDNS unavailable; manual connection still works")
        yield
        if advertisement:
            await asyncio.to_thread(advertisement.close)

    app = FastAPI(title="IoTDesign", version="1.0.0", lifespan=lifespan)
    app.state.db, app.state.settings, app.state.pairing = db, settings, pairing
    app.state.server_id, app.state.fingerprint = server_id, fingerprint
    bearer = HTTPBearer(auto_error=False)

    def error(status, code):
        raise HTTPException(status, detail=code)

    def client(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if not auth or auth.scheme.lower() != "bearer":
            error(401, "authentication_required")
        with db.connect() as connection:
            row = connection.execute("SELECT id FROM clients WHERE token_hash=?",
                                     (token_hash(auth.credentials),)).fetchone()
        if not row:
            error(401, "invalid_credentials")
        return row[0]

    def local(request: Request):
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            error(403, "desktop_only")

    def owned_scan(connection, scan_id, client_id):
        row = connection.execute("SELECT * FROM scans WHERE id=? AND client_id=?",
                                 (scan_id, client_id)).fetchone()
        if not row:
            error(404, "scan_not_found")
        return row

    @app.get("/api/health", response_model=Health)
    def health():
        return Health(server_id=server_id, name=settings.name, fingerprint=fingerprint,
                      ready=db.worker_ready(settings.worker_stale_seconds))

    @app.post("/api/pair", response_model=PairResponse)
    def pair(body: PairRequest, request: Request):
        failure = pairing.check(request.client.host if request.client else "unknown", body.code)
        if failure:
            error(429 if failure == "rate_limited" else 401, failure)
        token, client_id = secrets.token_urlsafe(32), str(uuid.uuid4())
        with db.connect() as connection:
            connection.execute("INSERT INTO clients VALUES (?,?,?)",
                               (client_id, token_hash(token), body.name))
        return PairResponse(token=token, client_id=client_id, server_id=server_id)

    @app.delete("/api/pair", status_code=204)
    def revoke(client_id: Annotated[str, Depends(client)]):
        with db.connect() as connection:
            connection.execute("DELETE FROM clients WHERE id=?", (client_id,))

    @app.post("/api/scans", response_model=ScanResponse, status_code=201)
    def create_scan(body: ScanRequest, client_id: Annotated[str, Depends(client)]):
        scan_id, created_at = str(uuid.uuid4()), now()
        with db.connect() as connection:
            connection.execute("INSERT INTO scans(id,client_id,name,created_at) VALUES (?,?,?,?)",
                               (scan_id, client_id, body.name, created_at))
            if body.capture_mode == 'stream':
                connection.execute('INSERT INTO stream_sessions(scan_id) VALUES (?)', (scan_id,))
        return ScanResponse(id=scan_id, name=body.name, state="capturing", created_at=created_at,
                            frames=[], capture_mode=body.capture_mode)

    @app.get("/api/scans", response_model=list[ScanResponse])
    def list_scans(client_id: Annotated[str, Depends(client)], include_frames: bool = True):
        with db.connect() as connection:
            ids = [r[0] for r in connection.execute(
                "SELECT id FROM scans WHERE client_id=? ORDER BY created_at DESC", (client_id,))]
        return [scan(scan_id, client_id, include_frames=include_frames) for scan_id in ids]

    @app.get("/api/scans/{scan_id}", response_model=ScanResponse)
    def scan(scan_id: str, client_id: Annotated[str, Depends(client)], include_frames: bool = True):
        with db.connect() as connection:
            row = owned_scan(connection, scan_id, client_id)
            frames = [dict(r) for r in connection.execute(
                "SELECT id,timestamp,width,height,sha256 FROM frames WHERE scan_id=? ORDER BY timestamp,id",
                (scan_id,))] if include_frames else []
            frame_count = connection.execute('SELECT COUNT(*) FROM frames WHERE scan_id=?', (scan_id,)).fetchone()[0]
            job = connection.execute("SELECT id FROM jobs WHERE scan_id=? ORDER BY created_at DESC LIMIT 1",
                                     (scan_id,)).fetchone()
            streaming = connection.execute('SELECT 1 FROM stream_sessions WHERE scan_id=?',
                                           (scan_id,)).fetchone() is not None
        return ScanResponse(**{k: row[k] for k in ("id", "name", "state", "created_at")},
                            frames=frames, frame_count=frame_count, latest_job_id=job[0] if job else None,
                            capture_mode='stream' if streaming else 'photos')

    @app.post('/api/scans/{scan_id}/tracking')
    def upload_tracking(scan_id: str, body: TrackingSample | TrackingBatch,
                        client_id: Annotated[str, Depends(client)]):
        records = body.records if isinstance(body, TrackingBatch) else [body]
        for record in records:
            if record.tracking_state == 'TRACKING' and (
                    record.camera_to_world is None or record.ar_timestamp_ns is None):
                error(422, 'tracked_pose_required')
        with db.connect(immediate=True) as connection:
            if owned_scan(connection, scan_id, client_id)['state'] != 'capturing':
                error(409, 'scan_frozen')
            if not connection.execute('SELECT 1 FROM stream_sessions WHERE scan_id=?',
                                      (scan_id,)).fetchone():
                error(409, 'not_a_stream_scan')
            folder = settings.data / 'scans' / scan_id / 'tracking'
            folder.mkdir(parents=True, exist_ok=True)
            pending = {}
            # Validate conflicts across the entire batch before writing files.
            for record in records:
                path = folder / f'video-{record.session_id}-{record.video_frame:09}.json'
                text = record.model_dump_json()
                if (path.exists() and path.read_text(encoding='utf-8') != text) or (
                        path in pending and pending[path] != text):
                    error(409, 'tracking_frame_conflict')
                pending[path] = text
            for path, text in pending.items():
                if not path.exists():
                    temporary = path.with_suffix('.tmp')
                    temporary.write_text(text, encoding='utf-8')
                    temporary.replace(path)
        return {'ok': True}

    @app.post('/api/scans/{scan_id}/video')
    async def video_upload(scan_id: str, request: Request,
                           client_id: Annotated[str, Depends(client)]):
        if request.headers.get('content-type', '').split(';')[0] != 'video/h264':
            error(415, 'h264_required')
        with db.connect() as connection:
            row = owned_scan(connection, scan_id, client_id)
            if row['state'] != 'capturing':
                error(409, 'scan_frozen')
            if not connection.execute('SELECT 1 FROM stream_sessions WHERE scan_id=?',
                                      (scan_id,)).fetchone():
                error(409, 'not_a_stream_scan')
        if scan_id in video_sessions or len(video_sessions) >= 4:
            error(409, 'video_already_active_or_capacity_reached')
        video_sessions.add(scan_id)
        receiver = None
        try:
            session_id = request.headers.get('x-video-session')
            if session_id is not None and not re.fullmatch(r'[a-f0-9]{32}', session_id):
                error(422, 'invalid_video_session')
            if session_id and (settings.data / 'scans' / scan_id / 'video' / f'{session_id}.h264').exists():
                error(409, 'video_session_already_recorded')
            try:
                rotation = int(request.headers.get('x-video-rotation', '0'))
            except ValueError:
                error(422, 'invalid_video_rotation')
            if rotation not in (0, 90, 180, 270):
                error(422, 'invalid_video_rotation')
            receiver = VideoReceiver(db, settings, previews, scan_id, client_id,
                                     session_id=session_id, rotation=rotation)
            async for chunk in request.stream():
                if chunk:
                    # Bound decoder work per call; never collect a whole request in RAM.
                    for offset in range(0, len(chunk), 64 * 1024):
                        await asyncio.to_thread(receiver.feed, chunk[offset:offset + 64 * 1024])
            await asyncio.to_thread(receiver.finish, True)
            if not receiver.decoded:
                error(422, 'empty_video')
            return {'video_id': receiver.id, 'bytes': receiver.bytes,
                    'decoded_frames': receiver.decoded, 'reconstruction_views': receiver.saved}
        except (ValueError, av.FFmpegError) as exc:
            error(422, str(exc)[:160])
        finally:
            if receiver:
                await asyncio.to_thread(receiver.finish)
            video_sessions.discard(scan_id)
            previews.remove(scan_id)

    @app.put("/api/scans/{scan_id}/live")
    async def live_upload(scan_id: str, client_id: Annotated[str, Depends(client)],
                          image: Annotated[UploadFile, File()]):
        try:
            with db.connect() as connection:
                row = owned_scan(connection, scan_id, client_id)
                if row['state'] != 'capturing':
                    error(409, 'scan_frozen')
            content = await image.read(512 * 1024 + 1)
            if len(content) > 512 * 1024:
                error(413, 'preview_too_large')
            try:
                with Image.open(io.BytesIO(content)) as decoded:
                    if decoded.format != 'JPEG' or max(decoded.size) > 640:
                        error(422, 'invalid_preview')
                    decoded.load()
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                error(422, 'invalid_preview')
            # Recheck state after reading/decoding: freezing may have happened meanwhile.
            with db.connect() as connection:
                if owned_scan(connection, scan_id, client_id)['state'] != 'capturing':
                    error(409, 'scan_frozen')
                previews.put(scan_id, client_id, content)
            return {'ok': True}
        finally:
            await image.close()

    @app.delete("/api/scans/{scan_id}/live", status_code=204)
    def stop_live(scan_id: str, client_id: Annotated[str, Depends(client)]):
        with db.connect() as connection:
            owned_scan(connection, scan_id, client_id)
        previews.remove(scan_id)

    @app.get("/desktop/scans/{scan_id}/live", dependencies=[Depends(local)])
    def desktop_live(scan_id: str):
        frame = previews.get(scan_id)
        with db.connect() as connection:
            valid = connection.execute(
                "SELECT 1 FROM scans JOIN clients ON scans.client_id=clients.id "
                "WHERE scans.id=? AND scans.state='capturing'", (scan_id,)).fetchone()
        if not valid or not frame:
            previews.remove(scan_id)
            return Response(status_code=204, headers={'Cache-Control': 'no-store'})
        return Response(frame.jpeg, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.put("/api/scans/{scan_id}/frames/{frame_id}", response_model=FrameResponse)
    async def frame(scan_id: str, frame_id: str, client_id: Annotated[str, Depends(client)],
                    image: Annotated[UploadFile, File()], timestamp: Annotated[str, Form()],
                    width: Annotated[int, Form()], height: Annotated[int, Form()]):
        if not ID.fullmatch(frame_id):
            error(422, "invalid_frame_id")
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                error(422, "timestamp_requires_timezone")
            timestamp = parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            error(422, "invalid_timestamp")
        with db.connect() as connection:
            owned_scan(connection, scan_id, client_id)
        # Never load more than the limit into memory; disk spool is removed on every path.
        digest, size = hashlib.sha256(), 0
        with tempfile.TemporaryFile() as spool:
            try:
                while chunk := await image.read(64 * 1024):
                    size += len(chunk)
                    if size > settings.max_frame_bytes:
                        error(413, "frame_too_large")
                    digest.update(chunk)
                    spool.write(chunk)
            finally:
                await image.close()
            spool.seek(0)
            try:
                with Image.open(spool) as decoded:
                    if decoded.format != "JPEG":
                        error(422, "jpeg_required")
                    if decoded.size != (width, height) or max(decoded.size) > 12000:
                        error(422, "invalid_dimensions")
                    decoded.verify()
                spool.seek(0)
                with Image.open(spool) as decoded:
                    decoded.load()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                error(422, "invalid_image")
            sha = digest.hexdigest()
            with db.connect(immediate=True) as connection:
                row = owned_scan(connection, scan_id, client_id)
                previous = connection.execute("SELECT * FROM frames WHERE scan_id=? AND id=?",
                                              (scan_id, frame_id)).fetchone()
                if previous:
                    if (previous["sha256"], previous["timestamp"], previous["width"], previous["height"]) != (
                            sha, timestamp, width, height):
                        error(409, "frame_id_conflict")
                    return FrameResponse(**dict(previous))
                if row["state"] != "capturing":
                    error(409, "scan_frozen")
                count = connection.execute("SELECT COUNT(*) FROM frames WHERE scan_id=?", (scan_id,)).fetchone()[0]
                streaming = connection.execute('SELECT 1 FROM stream_sessions WHERE scan_id=?',
                                               (scan_id,)).fetchone() is not None
                if count >= (STREAM_FRAME_LIMIT if streaming else settings.max_frames):
                    error(409, "frame_limit_reached")
                folder = settings.data / "scans" / scan_id / "images"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{frame_id}.jpg"
                spool.seek(0)
                path.write_bytes(spool.read())
                connection.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?)",
                                   (scan_id, frame_id, sha, timestamp, width, height,
                                    str(path.relative_to(settings.data))))
                if streaming:
                    schedule_snapshot(connection, scan_id)
        return FrameResponse(id=frame_id, timestamp=timestamp, width=width, height=height, sha256=sha)

    @app.post('/api/scans/{scan_id}/stream/finish')
    def finish_stream(scan_id: str, client_id: Annotated[str, Depends(client)]):
        with db.connect(immediate=True) as connection:
            owned_scan(connection, scan_id, client_id)
            if scan_id in video_sessions:
                error(409, 'stop_video_before_finish')
            session = connection.execute('SELECT * FROM stream_sessions WHERE scan_id=?', (scan_id,)).fetchone()
            if not session:
                error(409, 'not_a_stream_scan')
            count = connection.execute('SELECT COUNT(*) FROM frames WHERE scan_id=?', (scan_id,)).fetchone()[0]
            if count < 8:
                error(422, 'at_least_8_frames_required')
            connection.execute('UPDATE stream_sessions SET stopped=1 WHERE scan_id=?', (scan_id,))
            if not session['stopped']:
                connection.execute("UPDATE scans SET state='frozen' WHERE id=?", (scan_id,))
            job_id = schedule_snapshot(connection, scan_id)
        previews.remove(scan_id)
        return {'ok': True, 'latest_job_id': job_id}

    @app.post("/api/scans/{scan_id}/reconstruct", response_model=JobResponse, status_code=202)
    def reconstruct(scan_id: str, body: ReconstructRequest, client_id: Annotated[str, Depends(client)]):
        return enqueue_reconstruction(scan_id, body, client_id)

    def enqueue_reconstruction(scan_id, body, client_id):
        with db.connect(immediate=True) as connection:
            owned_scan(connection, scan_id, client_id)
            streaming = connection.execute('SELECT 1 FROM stream_sessions WHERE scan_id=?',
                                           (scan_id,)).fetchone() is not None
            if streaming:
                stream_job = schedule_snapshot(connection, scan_id, force=True)
                if stream_job is None:
                    error(422, 'at_least_8_frames_required')
        if streaming:
            return db.job(stream_job)
        with db.connect(immediate=True) as connection:
            owned_scan(connection, scan_id, client_id)
            active = connection.execute("SELECT id FROM jobs WHERE scan_id=? AND state IN ('queued','running')",
                                        (scan_id,)).fetchone()
            if active:
                return db.job(active[0])
            count = connection.execute("SELECT COUNT(*) FROM frames WHERE scan_id=?", (scan_id,)).fetchone()[0]
            if count < 8:
                error(422, "at_least_8_frames_required")
            job_id = str(uuid.uuid4())
            connection.execute("UPDATE scans SET state='frozen' WHERE id=?", (scan_id,))
            connection.execute("INSERT INTO jobs(id,scan_id,state,stage,frame_count,created_at,updated_at) "
                               "VALUES (?,?,'queued','waiting_for_worker',?,?,?)",
                               (job_id, scan_id, min(count, body.frame_count), now(), now()))
        return db.job(job_id)

    @app.post("/desktop/scans/{scan_id}/retry", dependencies=[Depends(local)])
    def desktop_retry(scan_id: str):
        with db.connect() as connection:
            row = connection.execute("SELECT client_id FROM scans WHERE id=?", (scan_id,)).fetchone()
            if not row:
                error(404, "scan_not_found")
            failed = connection.execute("SELECT 1 FROM jobs WHERE scan_id=? AND state='failed' LIMIT 1",
                                        (scan_id,)).fetchone()
            if not failed:
                error(409, "no_failed_job_to_retry")
            count = connection.execute("SELECT COUNT(*) FROM frames WHERE scan_id=?", (scan_id,)).fetchone()[0]
        frame_count = min(count, 20 if count >= 20 else 12 if count >= 12 else 8)
        return enqueue_reconstruction(scan_id, ReconstructRequest(frame_count=frame_count), row[0])

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    def job(job_id: str, client_id: Annotated[str, Depends(client)]):
        result = db.job(job_id)
        if not result:
            error(404, "job_not_found")
        with db.connect() as connection:
            owned_scan(connection, result["scan_id"], client_id)
        return result

    def scene_dir(scan_id, client_id=None):
        with db.connect() as connection:
            if client_id:
                owned_scan(connection, scan_id, client_id)
            row = connection.execute("SELECT artifact_dir FROM jobs WHERE scan_id=? AND state='completed' "
                                     "ORDER BY updated_at DESC LIMIT 1", (scan_id,)).fetchone()
        if not row:
            error(404, "scene_not_ready")
        return settings.data / row[0]

    @app.get('/desktop/scans/{scan_id}/scene', dependencies=[Depends(local)])
    def desktop_scene(scan_id: str, job_id: str | None = None):
        with db.connect() as connection:
            if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
            active = connection.execute(
                'SELECT id,state,stage,error FROM jobs WHERE scan_id=? ORDER BY created_at DESC LIMIT 1',
                (scan_id,)).fetchone()
            completed = connection.execute(
                "SELECT id,metrics FROM jobs WHERE scan_id=? AND state='completed' "
                'AND (? IS NULL OR id=?) ORDER BY updated_at DESC LIMIT 1',
                (scan_id, job_id, job_id)).fetchone()
            if job_id is not None and not completed:
                error(404, 'scene_not_ready')
            received = connection.execute('SELECT COUNT(*) FROM frames WHERE scan_id=?', (scan_id,)).fetchone()[0]
            processed = connection.execute(
                "SELECT COUNT(DISTINCT i.frame_id) FROM job_inputs i JOIN jobs j ON j.id=i.job_id "
                "WHERE j.scan_id=? AND j.state='completed'", (scan_id,)).fetchone()[0]
        return {'received_frames': received, 'processed_frames': processed,
                'pending_frames': max(0, received - processed),
                'latest_job': dict(active) if active else None,
                'completed_job_id': completed['id'] if completed else None,
                'metrics': json.loads(completed['metrics']) if completed else None}

    @app.get('/desktop/scans/{scan_id}/reconstruction-timeline', dependencies=[Depends(local)])
    def desktop_reconstruction_timeline(scan_id: str):
        with db.connect() as connection:
            if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
        from .timeline import reconstruction_timeline
        return reconstruction_timeline(db, settings, scan_id)

    @app.get('/desktop/scans/{scan_id}/scene/points', dependencies=[Depends(local)])
    def desktop_points(scan_id: str, job_id: str):
        # Bind geometry to a completed job: status and geometry cannot race with a newer export.
        with db.connect() as connection:
            row = connection.execute("SELECT artifact_dir FROM jobs WHERE id=? AND scan_id=? "
                                     "AND state='completed'", (job_id, scan_id)).fetchone()
        if not row:
            error(404, 'scene_not_ready')
        import numpy as np
        with np.load(settings.data / row[0] / 'viewer.npz', allow_pickle=False) as arrays:
            points, colors = arrays['points'], arrays['colors']
            # Display every exported point; do not silently downsample geometry.
            payload = np.column_stack((points, colors / 255)).astype('<f4').tobytes()
        return Response(payload, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

    @app.get('/desktop/scans/{scan_id}/scene/cameras', dependencies=[Depends(local)])
    def desktop_scene_cameras(scan_id: str, job_id: str):
        with db.connect() as connection:
            row = connection.execute("SELECT artifact_dir FROM jobs WHERE id=? AND scan_id=? "
                                     "AND state='completed'", (job_id, scan_id)).fetchone()
        if not row:
            error(404, 'scene_not_ready')
        return json.loads((settings.data / row[0] / 'cameras.json').read_text(encoding='utf-8'))

    @app.get('/desktop/scans/{scan_id}/view', response_class=HTMLResponse, dependencies=[Depends(local)])
    def desktop_view(scan_id: str):
        with db.connect() as connection:
            if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
        return (Path(__file__).parent / 'scene.html').read_text(encoding='utf-8')

    @app.get('/desktop/scans/{scan_id}/camera-path', dependencies=[Depends(local)])
    def desktop_camera_path(scan_id: str, session_id: str | None = None, after_frame: int = 0):
        with db.connect() as connection:
            if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
        if not 0 <= after_frame <= 108001:
            error(422, 'invalid_frame_cursor')
        folder = settings.data / 'scans' / scan_id / 'tracking'
        sessions = sorted({p.name.split('-')[1] for p in folder.glob('video-*.json')})
        if session_id is None and sessions:
            session_id = max(sessions, key=lambda sid: max(
                p.stat().st_mtime_ns for p in folder.glob(f'video-{sid}-*.json')))
        if session_id is not None and not re.fullmatch(r'[a-f0-9]{32}', session_id):
            error(422, 'invalid_tracking_session')
        records = []
        if session_id:
            paths = sorted(p for p in folder.glob(f'video-{session_id}-*.json')
                           if int(p.stem.rsplit('-', 1)[1]) > after_frame)
            for path in paths[:1000]:
                record = json.loads(path.read_text(encoding='utf-8'))
                clock = record.get('camera_timestamp_ns') or record.get('ar_timestamp_ns')
                offset = abs(record['timestamp_ns'] - clock) if clock is not None else None
                record['clock_offset_ms'] = offset / 1e6 if offset is not None else None
                record['synchronized'] = offset is not None and offset <= 100_000_000
                events = record.pop('imu', [])
                record['imu_sample_count'] = len(events)
                record['imu_sensor_types'] = sorted({event['sensor_type'] for event in events})
                record['frame_id'] = path.stem
                records.append(record)
        return {'session_id': session_id, 'sessions': sessions, 'frames': records,
                'next_frame': records[-1]['video_frame'] if records else after_frame,
                'has_more': len(records) == 1000}

    @app.get('/desktop/scans/{scan_id}/player', response_class=HTMLResponse, dependencies=[Depends(local)])
    def desktop_player(scan_id: str):
        with db.connect() as connection:
            if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
        return (Path(__file__).parent / 'player.html').read_text(encoding='utf-8')

    @app.get('/desktop/scans/{scan_id}/recordings', dependencies=[Depends(local)])
    def desktop_recordings(scan_id: str):
        with db.connect() as connection:
            if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
        folder = settings.data / 'scans' / scan_id / 'video'
        recordings = []
        for state_path in sorted(folder.glob('*.playback/state.json'), key=lambda p: p.parent.stat().st_mtime):
            state = json.loads(state_path.read_text(encoding='utf-8'))
            recordings.append({'id': state_path.parent.stem, **state,
                               'ready': (state_path.parent / 'index.m3u8').exists()})
        return recordings

    @app.get('/desktop/scans/{scan_id}/recordings/{session_id}/{filename}', dependencies=[Depends(local)])
    def desktop_recording(scan_id: str, session_id: str, filename: str):
        if not re.fullmatch(r'[a-f0-9]{32}', session_id) or not re.fullmatch(
                r'index\.m3u8|init\.mp4|segment-[0-9]{6}\.m4s', filename):
            error(404, 'recording_not_found')
        path = settings.data / 'scans' / scan_id / 'video' / f'{session_id}.playback' / filename
        if not path.is_file():
            error(404, 'recording_not_found')
        media = 'application/vnd.apple.mpegurl' if filename.endswith('.m3u8') else 'video/mp4'
        return FileResponse(path, media_type=media, headers={'Cache-Control': 'no-store'})

    def read_objects(folder):
        if not (folder / 'objects.json').is_file() or not (folder / 'objects-review.json').is_file():
            error(404, 'object_review_not_ready')
        return {'segmentation': json.loads((folder / 'objects.json').read_text(encoding='utf-8')),
                'review': json.loads((folder / 'objects-review.json').read_text(encoding='utf-8'))}

    def review_objects(scan_id, body, client_id=None):
        from .object_geometry import apply_review
        folder = scene_dir(scan_id, client_id)
        with review_lock:
            read_objects(folder)
            try:
                result = apply_review(folder, body.excluded_ids, body.expected_revision)
            except ValueError as exc:
                error(409 if 'another window' in str(exc) else 422, str(exc))
            manifest = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
            with db.connect() as connection:
                connection.execute("UPDATE jobs SET metrics=? WHERE scan_id=? AND state='completed' "
                                   'AND artifact_dir=?',
                                   (json.dumps(manifest['metrics']), scan_id,
                                    str(folder.relative_to(settings.data))))
            return result

    def frame_preview(folder, index):
        data = read_objects(folder)['segmentation']
        if index < 0 or index >= len(data['frames']):
            error(404, 'frame_not_found')
        with ZipFile(folder / 'objects-previews.zip') as archive:
            image = archive.read(data['frames'][index]['preview_archive_member'])
        return Response(image, media_type='image/jpeg')

    def object_mask(folder, object_id):
        import numpy as np
        data = read_objects(folder)['segmentation']
        for frame in data['frames']:
            for obj in frame['objects']:
                if obj['id'] != object_id:
                    continue
                with np.load(folder / 'objects.npz', allow_pickle=False) as arrays:
                    mask = np.unpackbits(arrays[frame['mask_array']][obj['mask_index']], bitorder='little',
                                         count=frame['height'] * frame['width'])
                rgba = np.zeros((frame['height'], frame['width'], 4), dtype=np.uint8)
                rgba[:, :, :3] = obj.get('color_rgb', [120, 220, 140])
                rgba[:, :, 3] = mask.reshape(frame['height'], frame['width']) * 180
                output = io.BytesIO()
                Image.fromarray(rgba).save(output, format='PNG')
                return Response(output.getvalue(), media_type='image/png')
        error(404, 'object_not_found')

    @app.get('/api/scans/{scan_id}/objects')
    def objects(scan_id: str, client_id: Annotated[str, Depends(client)]):
        return read_objects(scene_dir(scan_id, client_id))

    @app.post('/api/scans/{scan_id}/objects/review')
    def save_object_review(scan_id: str, body: ObjectReviewRequest,
                           client_id: Annotated[str, Depends(client)]):
        return review_objects(scan_id, body, client_id)

    @app.get('/desktop/scans/{scan_id}/objects', dependencies=[Depends(local)])
    def desktop_objects(scan_id: str):
        return read_objects(scene_dir(scan_id))

    @app.get('/desktop/scans/{scan_id}/objects/frames/{index}', dependencies=[Depends(local)])
    def desktop_frame_preview(scan_id: str, index: int):
        return frame_preview(scene_dir(scan_id), index)

    @app.get('/desktop/scans/{scan_id}/objects/masks/{object_id}', dependencies=[Depends(local)])
    def desktop_object_mask(scan_id: str, object_id: str):
        return object_mask(scene_dir(scan_id), object_id)

    @app.get('/desktop/scans/{scan_id}/review', response_class=HTMLResponse, dependencies=[Depends(local)])
    def desktop_object_review(scan_id: str):
        read_objects(scene_dir(scan_id))
        return (Path(__file__).parent / 'review.html').read_text(encoding='utf-8')

    @app.post('/desktop/scans/{scan_id}/objects/review', dependencies=[Depends(local)])
    def desktop_save_object_review(scan_id: str, body: ObjectReviewRequest, request: Request):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            error(403, 'review_origin_not_allowed')
        return review_objects(scan_id, body)

    @app.get("/api/scans/{scan_id}/scene", response_model=SceneResponse)
    def scene(scan_id: str, client_id: Annotated[str, Depends(client)]):
        manifest = json.loads((scene_dir(scan_id, client_id) / "scene.json").read_text())
        manifest["artifacts"] = {k: f"/api/scans/{scan_id}/artifacts/{v}" for k, v in manifest["artifacts"].items()}
        return manifest

    @app.get("/api/scans/{scan_id}/artifacts/{filename}")
    def artifact(scan_id: str, filename: str, client_id: Annotated[str, Depends(client)]):
        if filename not in {"cloud.ply", "cameras.json", "preview.jpg", "scene.json", "viewer.npz",
                            "objects.json", "objects.npz", "objects-preview.jpg", "objects-previews.zip",
                            "objects-review.json", "cloud-unfiltered.ply"}:
            error(404, "artifact_not_found")
        path = scene_dir(scan_id, client_id) / filename
        if not path.is_file():
            error(404, "artifact_not_found")
        return FileResponse(path, filename=filename)

    # Desktop UI is loopback-only, including pairing codes and local scan inventory.
    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(local)])
    def desktop():
        return (Path(__file__).parent / "desktop.html").read_text(encoding="utf-8")

    @app.get("/desktop/status", dependencies=[Depends(local)])
    def desktop_status():
        with db.connect() as connection:
            scans = [dict(r) for r in connection.execute(
                "SELECT id,name,state,created_at,"
                "(SELECT COUNT(*) FROM frames WHERE scan_id=scans.id) AS frame_count "
                "FROM scans ORDER BY created_at DESC")]
            jobs = [db.job(r[0]) for r in connection.execute("SELECT id FROM jobs ORDER BY created_at DESC")]
            live_scan_ids = [row[0] for row in connection.execute(
                "SELECT scans.id FROM scans JOIN clients ON scans.client_id=clients.id "
                "WHERE scans.state='capturing'") if previews.get(row[0]) is not None]
        addresses = lan_addresses()
        return {"health": health(), "addresses": [f"https://{ip}:{settings.port}" for ip in addresses],
                "code": pairing.code, "pairing_seconds": max(0, int(pairing.expires - __import__('time').monotonic())),
                "scans": scans, "jobs": jobs, "live_scan_ids": live_scan_ids}

    @app.get('/desktop/scans/{scan_id}/photos', response_class=HTMLResponse, dependencies=[Depends(local)])
    def desktop_photos(scan_id: str):
        with db.connect() as connection:
            row = connection.execute('SELECT name FROM scans WHERE id=?', (scan_id,)).fetchone()
        if not row:
            error(404, 'scan_not_found')
        page = (Path(__file__).parent / 'photos.html').read_text(encoding='utf-8')
        return page.replace('SCAN_ID_PLACEHOLDER', html.escape(scan_id, quote=True)).replace(
            'SCAN_NAME_PLACEHOLDER', html.escape(row['name'], quote=True))

    @app.get('/desktop/scans/{scan_id}/frames', dependencies=[Depends(local)])
    def desktop_photos_list(scan_id: str):
        with db.connect() as connection:
            if not connection.execute('SELECT id FROM scans WHERE id=?', (scan_id,)).fetchone():
                error(404, 'scan_not_found')
            return [dict(row) for row in connection.execute(
                'SELECT id,timestamp,width,height FROM frames WHERE scan_id=? ORDER BY timestamp,id', (scan_id,))]

    @app.get('/desktop/scans/{scan_id}/frames/{frame_id}', dependencies=[Depends(local)])
    def desktop_photo(scan_id: str, frame_id: str, thumbnail: bool = False):
        with db.connect() as connection:
            row = connection.execute('SELECT path FROM frames WHERE scan_id=? AND id=?',
                                     (scan_id, frame_id)).fetchone()
        if not row:
            error(404, 'frame_not_found')
        path = settings.data / row['path']
        if not path.is_file():
            error(404, 'frame_not_found')
        headers = {'Cache-Control': 'no-store'}
        if not thumbnail:
            return FileResponse(path, media_type='image/jpeg', headers=headers)
        with Image.open(path) as image:
            image.thumbnail((320, 320))
            output = io.BytesIO()
            image.convert('RGB').save(output, format='JPEG', quality=75)
        return Response(output.getvalue(), media_type='image/jpeg', headers=headers)

    @app.post("/desktop/pairing", dependencies=[Depends(local)])
    def renew_pairing():
        pairing.rotate()
        return {"ok": True}

    @app.get("/desktop/pairing.png", dependencies=[Depends(local)])
    def pairing_qr():
        address = next(iter(lan_addresses()), "127.0.0.1")
        payload = pairing.payload(f"https://{address}:{settings.port}", server_id, fingerprint)
        output = io.BytesIO()
        qrcode.make(payload).save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/desktop/scans/{scan_id}/artifacts/{filename}", dependencies=[Depends(local)])
    def desktop_artifact(scan_id: str, filename: str, job_id: str | None = None):
        if filename not in {"cloud.ply", "cameras.json", "preview.jpg", "scene.json",
                            "objects.json", "objects.npz", "objects-preview.jpg", "objects-previews.zip",
                            "objects-review.json", "cloud-unfiltered.ply"}:
            error(404, "artifact_not_found")
        if job_id is None:
            folder = scene_dir(scan_id)
        else:
            with db.connect() as connection:
                row = connection.execute("SELECT artifact_dir FROM jobs WHERE id=? AND scan_id=? "
                                         "AND state='completed'", (job_id, scan_id)).fetchone()
            if not row:
                error(404, 'scene_not_ready')
            folder = settings.data / row['artifact_dir']
        path = folder / filename
        if not path.is_file():
            error(404, "artifact_not_found")
        return FileResponse(path, filename=filename)

    return app
