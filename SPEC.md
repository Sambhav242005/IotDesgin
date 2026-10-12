# IoTDesign specification

## Goal and release boundary

Capture overlapping room photographs on a native phone app, automatically reconnect to a trusted local PC, reconstruct an unscaled colored point cloud locally, and inspect/reload it on the PC. Android is the first target; iPhone shares the architecture but requires platform validation.

First-release defaults are same-network operation, one room per scan, selected JPEG uploads, local processing, and one active GPU reconstruction. Continuous video streaming, metrically accurate geometry, animated people, RF simulation, IoT placement, AR persistence, and Gaussian splats are later milestones.

## Required mobile behavior

1. On startup, check the saved address and discover servers for up to eight seconds.
2. Reconnect to the paired stable ID; authenticate before saving a changed address.
3. Offer pairing for one new server, selection for several servers, and manual host/port entry plus Retry Discovery when none are reachable.
4. Validate manual addresses with health/API-version and TLS-fingerprint checks. Save connection credentials only after successful pairing.
5. Persist trusted server identity, name, address, fingerprint, and credentials across restarts. Provide Change Server and Forget Server.
6. Recheck on foreground/network changes; retain pending uploads while disconnected. Show actionable permission, reachability, API, and authentication errors.
7. Provide rear-camera preview, Start, Pause/Resume, manual capture, Finish, thumbnails, and upload status. Capture approximately every two seconds, up to 30 images.
8. Save images and metadata before upload; remove queued images only after acknowledgment. Repeated uploads must not duplicate frames.

The requested capture target is 1280 x 720 with actual dimensions recorded. The current app instead limits the longest edge to 1280 while preserving aspect ratio. Exact capture-size behavior remains an implementation gap.

## Trust and network requirements

- Advertise `_iotdesign._tcp.local.` from the Windows host with stable identity, port, and API version.
- Pair using a desktop QR or expiring code. Authenticate HTTPS requests and pin the paired certificate.
- Keep pairing-code administration on the local desktop. Store mobile credentials using platform secure storage.
- Include Android camera/network permissions and iOS camera/local-network/Bonjour declarations.
- Document LAN HTTPS and Windows private-network firewall setup. Manual entry works only if the PC is reachable; it cannot bypass device isolation.

## API contract

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Identity, API version, worker readiness, certificate fingerprint |
| `POST /api/pair` | Exchange pairing code for credential |
| `DELETE /api/pair` | Revoke the current pairing |
| `POST /api/scans` | Create a scan |
| `GET /api/scans` | List this client's scans |
| `PUT /api/scans/{id}/frames/{frame_id}` | Idempotent multipart JPEG upload |
| `GET /api/scans/{id}` | Received frames and scan state |
| `POST /api/scans/{id}/reconstruct` | Freeze inputs and queue a job |
| `GET /api/jobs/{id}` | Job state, stage, metrics, failure |
| `GET /api/scans/{id}/scene` | Completed versioned scene manifest |
| `GET /api/scans/{id}/artifacts/{filename}` | Download an authorized artifact |

Authenticated routes enforce scan ownership. Accept at most 30 frames and 10 MiB per frame; validate decoded JPEG content and supplied dimensions. Persist queued, running, completed, and failed jobs. Preserve source images on interruption and permit retry.

`docs/openapi.json` is exported from the server. The original plan also requires generating mobile API types from that schema; the current app still uses manually declared response types.

## Reconstruction and viewer requirements

- Use official VGGT as the point-cloud baseline. Evaluate VGGT-Gaussian separately after checkpoint, licensing, installation, and output verification.
- Select frames evenly; preserve originals and resize model inputs independently.
- Retry GPU inference memory failure once with fewer frames. Maintain a single worker per data directory.
- Export colored PLY, estimated cameras, preview, and a versioned scene manifest. Label geometry unscaled.
- Record 8/12/20-frame benchmark duration, peak VRAM, RAM, input resolution, and point count.
- Support orbit, pan, zoom, reset, camera positions, downloads, and reload from saved artifacts.

The current GPU path runs natively on Windows. The original WSL2 worker requirement is deferred because the current locking implementation is Windows-specific. The 500,000-point cap is a current export design choice, not a required model output.

## Optional object segmentation

Official Meta SAM 2.1 Hiera Small can separate image regions using automatic masks. Enable it with `worker --segment-objects` or run `segment` on a JPEG directory. Export independent packed masks, frame metadata, bounding boxes, quality scores, and colored previews. Retain overlapping regions and frame-local IDs. Attach successful worker segmentation artifacts to the scene manifest and enforce authenticated artifact ownership. Failed optional segmentation leaves the job failed and source images recoverable.

`worker --semantic-objects` uses YOLO OpenImages Nano category proposals to prompt SAM 2 masks. Labels remain proposals. Source-image coordinates and frame provenance associate masks with exported points. The desktop mask-review page supports explicit exclusions and restoration from preserved base geometry; the viewer supports object colors and reload. Mirror/Window proposals do not establish trustworthy 3D centroids. Cross-frame identity, automatic movement/material detection, mirror completeness and measured IoT placement remain pending. The detector missed mirrors in all three sampled Mirror3D images; do not claim reliable automatic mirror removal. No VLM is used.

## Windows setup and hardware adjustment

For semantic mirror masks, compare the box baseline against bounded positive/negative point hypotheses and one logits-based refinement. Preserve chosen prompt provenance and distinguish heuristic points from confirmed annotations. Non-mirror prompting remains box-only. Keep exclusions manual and retain unknown material/motion states; mask ranking is not proof of mirror accuracy.

Semantic scans also run Grounding DINO Tiny with the prompt `mirror.` on CPU before SAM 2. Preserve prompt/model provenance and keep exclusions explicit. Targeted detection improved candidate recall on three Mirror3D samples, but mask accuracy remains insufficient for automatic filtering. Review does not establish completeness.

`Start-IoTDesign.cmd` invokes a Windows x64 installer/launcher. It bootstraps uv and Python 3.12 when missing, installs dependencies and pinned source archives, checks real CUDA execution and free memory, caches selected checkpoints, and starts background services. Preserve data, credentials, certificates and original images. Reject unrelated port owners; stop only verified launcher-owned processes. Private-network firewall setup is an explicit elevated option.

Auto selects High (12 images at 336 px), Balanced (8 at 224 px), Low (8 at 168 px, FP16 aggregator, SAM 2 Tiny), or ServerOnly. GPU profiles require at least 11 GiB available RAM and 3.1 GiB free VRAM. The GTX 1050 Ti target remains physically unvalidated. ServerOnly preserves capture and queued jobs but does not perform CPU reconstruction. Published quantization code is not enabled without a packed-weight Windows/Pascal backend and measured geometry validation.

Segmentation previews display colored masks and contours without bounding boxes. Future IoT understanding must keep object category, image mask, motion state, material state, 3D position, and placement constraints separately evidenced, including unknown states. See the [research synthesis](docs/research/README.md) for the proposed stages and validation cases.

## Acceptance evidence

Status recorded on 2026-10-12. [GATES.md](GATES.md) holds the existing validation gates; [benchmark report](docs/benchmarks/pexels-7578017.md) records GPU results.

| Acceptance area | Evidence and remaining work |
|---|---|
| API authentication, upload validation/idempotency, freezing, recovery | Existing API suite recorded as six passing tests |
| Mobile protocol logic and types | Existing protocol suite recorded as four passing tests; type check passed |
| Native Android build | Debug APK built successfully with custom NSD/pinned transport module |
| Emulator pairing and camera upload | App launched, manually paired through host gateway, and uploaded 30 virtual-camera frames; test scan left unfinished |
| First-launch real-phone discovery/pairing | Pending physical Android test |
| Saved reconnect and changed-IP recovery | Logic implemented; physical network/restart validation pending |
| Multiple servers and unrelated-server rejection | Physical integration validation pending |
| Permission denial, multicast blocking, shutdown, Wi-Fi changes | Physical-device scenario validation pending |
| Interrupted uploads resume without loss/duplicates | Server idempotency covered; full mobile interruption test pending |
| GPU memory failure and worker restart | Retry/recovery implemented; forced GPU-failure validation pending |
| 8/12/20 GPU benchmark | Pexels clip completed; see report for timing and memory limits |
| Recognizable room from at least 12 received images | Pending a real multi-view room scan; near-static Pexels clip failed the quality goal |
| Saved-scene navigation/reload | Benchmark scene loaded in Viser and the Three.js showcase |
| Optional SAM 2 image-region masks | 12 video frames yielded 128 masks; packed-mask and artifact-ownership tests pass; see SAM 2 benchmark report |
| iPhone build and device behavior | Swift module exists; macOS/Xcode build and device validation pending |

Emulator and stock-video evidence cannot close the real-phone/real-room acceptance gates. Future changes should record their actual validation source and preserve that distinction.
