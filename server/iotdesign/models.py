from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat


class ImuSample(BaseModel):
    sensor_type: int
    timestamp_ns: int = Field(ge=0)
    accuracy: int
    values: list[FiniteFloat] = Field(min_length=3, max_length=5)


class FloorEstimate(BaseModel):
    origin: list[FiniteFloat] = Field(min_length=3, max_length=3)
    normal: list[FiniteFloat] = Field(min_length=3, max_length=3)


class TrackingSample(BaseModel):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    video_frame: int = Field(ge=1, le=108001)
    timestamp_ns: int = Field(ge=0)
    ar_timestamp_ns: int | None = Field(default=None, ge=0)
    encoder_timestamp_ns: int | None = Field(default=None, ge=0)
    camera_timestamp_ns: int | None = Field(default=None, ge=0)
    timestamp_clock: Literal['android_camera', 'elapsed_realtime_normalized', 'camera_realtime_from_encoder_uptime'] | None = None
    encoder_uptime_offset_ns: int | None = None
    sensor_timestamp_source: Literal['REALTIME', 'UNKNOWN'] | None = None
    tracking_state: Literal['TRACKING', 'PAUSED', 'STOPPED', 'INITIALIZING']
    tracking_failure: str | None = Field(default=None, max_length=100)
    camera_to_world: list[FiniteFloat] | None = Field(default=None, min_length=16, max_length=16)
    translation: list[FiniteFloat] | None = Field(default=None, min_length=3, max_length=3)
    quaternion_xyzw: list[FiniteFloat] | None = Field(default=None, min_length=4, max_length=4)
    floor: FloorEstimate | None = None
    imu: list[ImuSample] = Field(default_factory=list, max_length=512)


class TrackingBatch(BaseModel):
    records: list[TrackingSample] = Field(min_length=1, max_length=30)


class Health(BaseModel):
    server_id: str
    name: str
    api_version: Literal[1] = 1
    ready: bool
    fingerprint: str


class ObjectReviewRequest(BaseModel):
    excluded_ids: list[str] = Field(default_factory=list, max_length=1920)
    expected_revision: int = Field(ge=0)


class PairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=8)
    name: str = Field(default="Phone", min_length=1, max_length=80)


class PairResponse(BaseModel):
    token: str
    client_id: str
    server_id: str


class ScanRequest(BaseModel):
    name: str = Field(default="Room scan", min_length=1, max_length=120)
    capture_mode: Literal['photos', 'stream'] = 'photos'


class FrameResponse(BaseModel):
    id: str
    timestamp: str
    width: int
    height: int
    sha256: str


class ScanResponse(BaseModel):
    id: str
    name: str
    state: str
    created_at: str
    frames: list[FrameResponse]
    frame_count: int = 0
    latest_job_id: str | None = None
    capture_mode: Literal['photos', 'stream'] = 'photos'


class ReconstructRequest(BaseModel):
    frame_count: Literal[8, 12, 20] = 12


class JobResponse(BaseModel):
    id: str
    scan_id: str
    state: Literal["queued", "running", "completed", "failed"]
    stage: str
    frame_count: int
    created_at: str
    updated_at: str
    error: str | None = None
    metrics: dict | None = None


class SceneResponse(BaseModel):
    version: Literal[1] = 1
    scan_id: str
    job_id: str
    representation: Literal["point_cloud"] = "point_cloud"
    coordinates: Literal["unscaled"] = "unscaled"
    camera_convention: Literal["opencv_camera_to_world"] = "opencv_camera_to_world"
    artifacts: dict[str, str]
    metrics: dict
