"""Bounded, disposable camera previews; never reconstruction inputs."""

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Preview:
    client_id: str
    received: float
    jpeg: bytes


class LivePreviews:
    ttl = 3.0
    max_streams = 16

    def __init__(self):
        self._frames: dict[str, Preview] = {}
        self._lock = threading.Lock()

    def put(self, scan_id: str, client_id: str, jpeg: bytes):
        with self._lock:
            current = time.monotonic()
            self._frames = {key: value for key, value in self._frames.items()
                            if current - value.received < self.ttl and value.client_id != client_id}
            if len(self._frames) >= self.max_streams:
                oldest = min(self._frames, key=lambda key: self._frames[key].received)
                del self._frames[oldest]
            self._frames[scan_id] = Preview(client_id, current, jpeg)

    def get(self, scan_id: str):
        with self._lock:
            frame = self._frames.get(scan_id)
            if frame and time.monotonic() - frame.received < self.ttl:
                return frame
            self._frames.pop(scan_id, None)
            return None

    def remove(self, scan_id: str):
        with self._lock:
            self._frames.pop(scan_id, None)
