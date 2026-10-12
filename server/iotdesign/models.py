from typing import Literal

from pydantic import BaseModel, Field


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
    latest_job_id: str | None = None


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
