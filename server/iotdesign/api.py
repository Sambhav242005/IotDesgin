import asyncio
import hashlib
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

import qrcode
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from PIL import Image, UnidentifiedImageError

from .config import Settings
from .db import Database, now
from .discovery import Advertisement
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
)
from .security import Pairing, ensure_certificate, lan_addresses, token_hash

log = logging.getLogger(__name__)
ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def create_app(settings: Settings | None = None):
    review_lock = threading.Lock()
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
        return ScanResponse(id=scan_id, name=body.name, state="capturing", created_at=created_at, frames=[])

    @app.get("/api/scans", response_model=list[ScanResponse])
    def list_scans(client_id: Annotated[str, Depends(client)]):
        with db.connect() as connection:
            ids = [r[0] for r in connection.execute(
                "SELECT id FROM scans WHERE client_id=? ORDER BY created_at DESC", (client_id,))]
        return [scan(scan_id, client_id) for scan_id in ids]

    @app.get("/api/scans/{scan_id}", response_model=ScanResponse)
    def scan(scan_id: str, client_id: Annotated[str, Depends(client)]):
        with db.connect() as connection:
            row = owned_scan(connection, scan_id, client_id)
            frames = [dict(r) for r in connection.execute(
                "SELECT id,timestamp,width,height,sha256 FROM frames WHERE scan_id=? ORDER BY timestamp,id",
                (scan_id,))]
            job = connection.execute("SELECT id FROM jobs WHERE scan_id=? ORDER BY created_at DESC LIMIT 1",
                                     (scan_id,)).fetchone()
        return ScanResponse(**{k: row[k] for k in ("id", "name", "state", "created_at")},
                            frames=frames, latest_job_id=job[0] if job else None)

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
                if count >= settings.max_frames:
                    error(409, "frame_limit_reached")
                folder = settings.data / "scans" / scan_id / "images"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{frame_id}.jpg"
                spool.seek(0)
                path.write_bytes(spool.read())
                connection.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?)",
                                   (scan_id, frame_id, sha, timestamp, width, height,
                                    str(path.relative_to(settings.data))))
        return FrameResponse(id=frame_id, timestamp=timestamp, width=width, height=height, sha256=sha)

    @app.post("/api/scans/{scan_id}/reconstruct", response_model=JobResponse, status_code=202)
    def reconstruct(scan_id: str, body: ReconstructRequest, client_id: Annotated[str, Depends(client)]):
        return enqueue_reconstruction(scan_id, body, client_id)

    def enqueue_reconstruction(scan_id, body, client_id):
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
            scans = [dict(r) for r in connection.execute("SELECT id,name,state,created_at FROM scans ORDER BY created_at DESC")]
            jobs = [db.job(r[0]) for r in connection.execute("SELECT id FROM jobs ORDER BY created_at DESC")]
        addresses = lan_addresses()
        return {"health": health(), "addresses": [f"https://{ip}:{settings.port}" for ip in addresses],
                "code": pairing.code, "pairing_seconds": max(0, int(pairing.expires - __import__('time').monotonic())),
                "scans": scans, "jobs": jobs}

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
    def desktop_artifact(scan_id: str, filename: str):
        if filename not in {"cloud.ply", "cameras.json", "preview.jpg", "scene.json",
                            "objects.json", "objects.npz", "objects-preview.jpg", "objects-previews.zip",
                            "objects-review.json", "cloud-unfiltered.ply"}:
            error(404, "artifact_not_found")
        path = scene_dir(scan_id) / filename
        if not path.is_file():
            error(404, "artifact_not_found")
        return FileResponse(path, filename=filename)

    return app
