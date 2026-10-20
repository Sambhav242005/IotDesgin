"""Continuous Annex-B H.264 ingestion; model views are extracted on the PC."""
import hashlib
import io
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import av

from .playback import VideoPlayback
from .streaming import schedule_snapshot


class VideoReceiver:
    def __init__(self, db, settings, previews, scan_id, client_id, session_id=None, rotation=0):
        self.db, self.settings, self.previews = db, settings, previews
        self.scan_id, self.client_id = scan_id, client_id
        self.id = session_id or uuid.uuid4().hex
        self.folder = settings.data / 'scans' / scan_id / 'video'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / f'{self.id}.h264'
        self.file = self.path.open('xb')
        self.rotation = rotation
        self.playback = VideoPlayback(self.path, rotation)
        self.codec = av.CodecContext.create('h264', 'r')
        self.codec.thread_count = 2
        self.started = datetime.now(timezone.utc)
        self.clock = time.monotonic()
        self.bytes = self.decoded = self.saved = 0
        self.bytes_at_decode = 0
        self.last_preview = -1.0
        self.closed = False

    def feed(self, chunk):
        if self.bytes + len(chunk) > 2 * 1024**3 or time.monotonic() - self.clock > 3600:
            raise ValueError('video_session_limit_reached')
        # Keep the encoded source, including a recoverable partial stream on disconnect.
        self.file.write(chunk)
        self.file.flush()
        self.bytes += len(chunk)
        self.playback.feed(chunk)
        for packet in self.codec.parse(chunk):
            for frame in self.codec.decode(packet):
                self.accept(frame)
        # A sender cannot fill the parser with an unbounded incomplete NAL.
        if self.bytes - self.bytes_at_decode > 4 * 1024**2:
            raise ValueError('invalid_video')

    def accept(self, frame):
        if frame.width > 1920 or frame.height > 1920:
            raise ValueError('video_resolution_too_large')
        self.decoded += 1
        self.bytes_at_decode = self.bytes
        elapsed = (self.decoded - 1) / 30.0
        # Every decoded picture is a durable reconstruction input.
        preserve = True
        preview = time.monotonic() - self.last_preview >= 0.1
        if not preserve and not preview:
            return
        image = frame.to_image()
        with self.db.connect(immediate=True) as connection:
            valid = connection.execute(
                'SELECT 1 FROM scans JOIN clients ON clients.id=scans.client_id '
                "WHERE scans.id=? AND scans.client_id=? AND scans.state='capturing'",
                (self.scan_id, self.client_id)).fetchone()
            if not valid:
                raise ValueError('scan_frozen_or_pairing_revoked')
            if preview:
                thumb = image.rotate(-self.rotation, expand=True)
                thumb.thumbnail((640, 640))
                buf = io.BytesIO()
                thumb.save(buf, 'JPEG', quality=70)
                self.previews.put(self.scan_id, self.client_id, buf.getvalue())
                self.last_preview = time.monotonic()
            if preserve:
                frame_id = f'video-{self.id}-{self.decoded:09}'
                folder = self.settings.data / 'scans' / self.scan_id / 'images'
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f'{frame_id}.jpg'
                image.save(path, 'JPEG', quality=92)
                connection.execute('INSERT INTO frames VALUES (?,?,?,?,?,?,?)', (
                    self.scan_id, frame_id, hashlib.sha256(path.read_bytes()).hexdigest(),
                    (self.started + timedelta(seconds=elapsed)).isoformat(), image.width, image.height,
                    str(path.relative_to(self.settings.data))))
                self.saved += 1
                schedule_snapshot(connection, self.scan_id)

    def finish(self, complete=False):
        if self.closed:
            return
        success = False
        try:
            if complete:
                for packet in self.codec.parse(b''):
                    for frame in self.codec.decode(packet):
                        self.accept(frame)
                for frame in self.codec.decode(None):
                    self.accept(frame)
                success = self.decoded > 0
        finally:
            self.file.close()
            self.playback.close()
            self.closed = True
            self.path.with_suffix('.json').write_text(json.dumps({
                'id': self.id, 'codec': 'h264', 'fps_contract': 30, 'bytes': self.bytes,
                'decoded_frames': self.decoded, 'reconstruction_views': self.saved,
                'reconstruction_sampling': 'every_decoded_frame',
                'started_at': self.started.isoformat(), 'complete': success,
            }, indent=2), encoding='utf-8')
