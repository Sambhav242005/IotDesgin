from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8443
    name: str = "IoTDesign PC"
    advertise: bool = True
    max_frame_bytes: int = 10 * 1024 * 1024
    max_frames: int = 30
    worker_stale_seconds: int = 120

    def __post_init__(self):
        self.data = self.data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self):
        return self.data / "iotdesign.sqlite3"
