# IoTDesign specification

## Goal and release boundary

Stream continuous encoded camera video from a native Android phone to a trusted local PC, preserve the received video locally, reconstruct successive unscaled colored point clouds from PC-decoded views, and inspect them while streaming. iOS retains the photo path pending native video implementation.

Android video transport is a persistent chunked, authenticated, certificate-pinned HTTPS request carrying Annex-B H.264. An ARCore shared Camera2 session supplies a hardware MediaCodec encoder directly at a requested 1280x720 / 30 fps / 2.5 Mbit/s, with no audio. Actual cadence depends on the camera and hardware. The phone does not take timed JPEGs for stream scans. VGGT consumes every decoded picture on the PC in consecutive overlapping batches; continuous video transport does not imply frame-rate SLAM or fused geometry.

## Required mobile behavior

1. On startup, check the saved address and discover servers for up to eight seconds.
2. Reconnect to the paired stable ID; authenticate before saving a changed address.
3. Offer pairing for one new server, selection for several servers, and manual host/port entry plus Retry Discovery when none are reachable.
4. Validate manual addresses with health/API-version and TLS-fingerprint checks. Save connection credentials only after successful pairing.
5. Persist trusted server identity, name, address, fingerprint, and credentials across restarts. Provide Change Server and Forget Server.
6. Recheck on foreground/network changes; retain pending uploads while disconnected. Show actionable permission, reachability, API, and authentication errors.
7. Provide rear-camera preview, Start, Pause/Resume, Finish and video transport status. Android stream scans send continuous H.264; the manual photo control is absent. Retain manual/timed capture and the 30-photo queue for existing photo scans and iOS.
8. For photo scans, save images/metadata before upload and remove queued images only after acknowledgment; retries remain idempotent. For video, preserve all received encoded bytes on the PC. A disconnect creates a gap, never a fabricated or replayed live feed; resume opens a new independently decodable source file.

Android video requests target 1280x720; the decoder records actual selected-view dimensions. Original H.264 is retained alongside derived JPEGs. The phone does not maintain an offline video retry queue in this implementation. Lost network intervals require explicit resume and remain gaps. A native rebuild/install is required.

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
| `POST /api/scans/{id}/video` | Continuous authenticated H.264 request; one active session per scan |
| `PUT /api/scans/{id}/live` | Authenticated disposable JPEG preview, at most 640 pixels / 512 KiB |
| `DELETE /api/scans/{id}/live` | Clear the owning phone's preview |
| `POST /api/scans/{id}/reconstruct` | Freeze inputs and queue a job |
| `GET /api/jobs/{id}` | Job state, stage, metrics, failure |
| `GET /api/scans/{id}/scene` | Completed versioned scene manifest |
| `GET /api/scans/{id}/artifacts/{filename}` | Download an authorized artifact |

Authenticated routes enforce scan ownership. The legacy JPEG route accepts at most 30 photo frames or 3,600 stream JPEG uploads, and 10 MiB per frame. The video decoder retains every frame without that derived-frame limit; validate decoded JPEG content and supplied dimensions. Persist queued, running, completed, and failed jobs. Preserve source images on interruption and permit retry.

The video endpoint requires an owned capturing stream scan, `video/h264`, and the existing pinned HTTPS trust. Receive/decode incrementally, preserve bytes under `scans/<id>/video/<session>.h264`, and save a JSON receipt. Limit four concurrent sessions process-wide, one per scan, two GiB or one hour per connection, and decoded dimensions up to 1920 on each axis. Decoder work runs off the API event loop with two threads per decoder. Preserve partial files on interruption. Finish must wait for the video request's receipt before freezing the scan.

The PC preserves every decoded video picture as a reconstruction input and exposes received/processed/pending counts. Browser playback uses a real video element and HLS.js 1.7.3 (or native HLS): FFmpeg copies incoming H.264 into fMP4 segments with a complete retained playlist. Provide pause, rewind, seek, session selection and Go live. This is buffered playback, not zero-latency video. Received source video is preserved separately; sessions remain independent across gaps. FFmpeg must be on PATH or configured with IOTDESIGN_FFMPEG. Missing playback support reports an explicit error. Legacy photo previews retain their disposable JPEG route. All desktop playback reads are loopback-only.

`docs/openapi.json` is exported from the server. The original plan also requires generating mobile API types from that schema; the current app still uses manually declared response types.

## Reconstruction and viewer requirements

The loopback PC dashboard must show each scan's saved-photo count and a captured-photo gallery, including unfinished scans. The gallery uses thumbnails and opens preserved original JPEGs on selection. New uploads appear automatically. Gallery metadata, thumbnails, and originals remain desktop-only; no phone credentials or filesystem paths are embedded in the page.

- Use official VGGT as the point-cloud baseline. Evaluate VGGT-Gaussian separately after checkpoint, licensing, installation, and output verification.
- Select frames evenly; preserve originals and resize model inputs independently.
- Retry GPU inference memory failure once with fewer frames. Maintain a single worker per data directory.
- Export colored PLY, estimated cameras, preview, and a versioned scene manifest. Label geometry unscaled.
- Record 8/12/20-frame benchmark duration, peak VRAM, RAM, input resolution, and point count.
- Support orbit, pan, zoom, reset, camera positions, downloads, and reload from saved artifacts.

The current GPU path runs natively on Windows. The original WSL2 worker requirement is deferred because the current locking implementation is Windows-specific. There is no default fixed point-count cap. Keep every valid filtered model point; available memory and model resolution still bound each inference result.

## Reconstruction replay and independent phone-pose diagnostics

The desktop reconstruction view places retained source video beside the point cloud. A source-frame strip exposes received, processing, completed and failed input states. Selecting a frame or image-estimated camera links the source image and camera; completed-batch controls replay actual saved job outputs. Independent unaligned batches replace one another; only accepted same-origin maps accumulate. Thumbnail image planes are camera markers, not reconstructed surfaces. Historical downloads are bound to the selected completed job.

The retained raw-H.264 remux generates a nominal 30 fps media timeline. Its source-picture ordinal maps to playback time; it is not measured capture cadence or a sensor timestamp. Recorded capture elapsed time and its clock provenance remain separate. Missing historical pictures do not become fabricated source observations.

An optional CPU PyCOLMAP diagnostic reconstructs saved images without phone priors, preserves frozen image identities and hashes, and compares image-estimated OpenCV camera-to-world matrices with synchronized ARCore matrices per origin. Keep the 100 ms timestamp gate, registration/fit coverage, sufficient movement and geometric-support checks. Reports distinguish failed reconstruction, inconclusive evidence, rejected pose agreement and consistency. Similarity-fit agreement is not ground truth, absolute position accuracy, measured scale or raw IMU validation. Diagnostics preserve the scan state, original files and production VGGT jobs; explicit diagnostic sampling does not change all-frame production ingestion.

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


The desktop chooses a scan with a fresh live preview rather than sticking to an empty unfinished scan. Fresh preview scan IDs are included in loopback status. The phone displays the actual preview error when capture or upload fails.

## Progressive live reconstruction (2026-10-18)

`POST /api/scans` accepts `capture_mode: "stream"` (default `"photos"` for compatibility). Android stream scans use continuous encoded video. The PC preserves source video and derives JPEG reconstruction views after decoding. Pause/backgrounding closes the current video connection. Resume starts a new H.264 session in the same scan; no timed JPEG upload is used. Every decoded picture remains a durable reconstruction input; source-session byte/time limits still apply.

After eight durable frames, queue the first immutable GPU snapshot. While the stream remains open, queue another snapshot after at least four additional frames, coalescing arrivals behind one active job per scan. Process consecutive windows of at most eight views, advancing four new frames per job with overlapping context. Finish drains the final short tail. Never subsample a stream job or retry with fewer frames. A failed batch blocks subsequent work until its exact inputs are retried. Frames wait durably when GPU inference falls behind; using all incoming pictures is not a 30 Hz reconstruction guarantee. Every job stores its own ordered input frame IDs. Accepted synchronized ARCore-aligned snapshots accumulate in a per-origin live map with spatial deduplication and no fixed total-point cap. Unaligned results remain independent estimates. This is tracked batch accumulation, not video-rate dense SLAM. Update latency depends on input cadence, queued work and GPU inference. Progressive jobs skip optional segmentation to prioritize geometry updates.

`POST /api/scans/{id}/stream/finish` freezes capture after the phone stops video and receives the PC upload receipt. Once active work finishes, include any remaining new frames in one final snapshot, even if fewer than four arrived. Originals, prior jobs and completed artifacts remain preserved. Failure leaves the last completed scene available and permits explicit retry.

The dashboard's **Open live 3D** link opens `/desktop/scans/{id}/view`; its status and job-bound binary point endpoints are loopback-only. It automatically replaces the displayed cloud on completion, draws all exported points and links the full PLY. WebGL and CDN access for Three.js are currently required. Geometry polling and rendering pause when the page or scene container is hidden. Physical H.264 reception/decoding and video-driven GPU updates were verified on 2026-10-18; clean video-only room quality, reconnect and long-duration acceptance remain pending (GATES.md).


## Visual-inertial camera tracking (2026-10-18)

Android video capture now requires working ARCore shared-camera support. ARCore combines camera observations and phone IMU measurements to estimate physical camera position/orientation. The same camera session supplies continuous H.264. ARCore renders the native preview using display rotation and transformed texture coordinates; the raw camera buffer is not stretched into the phone viewfinder.

Send tracking metadata for every encoded picture, bundled in bounded six-picture messages before the corresponding encoded bytes. Preserve a one-to-one source-session/frame-index association. A native-generated session ID joins the H.264 source, decoded frame ID, ARCore timestamp and physical camera pose. Retain rotation vector, gyroscope, accelerometer and linear-acceleration samples with their monotonic timestamps. The tracking endpoint is authenticated, owned, capturing-stream-only, bounded and idempotent. Raw acceleration is never double-integrated into a purported camera position.

Use accepted physical camera poses to fit a similarity alignment of VGGT cameras and geometry into one ARCore session's coordinates. Convert OpenGL camera axes to OpenCV. Require at least three synchronized TRACKING cameras, at least 0.10 m estimated trajectory span, rotation consistency and bounded position residual; reject stale (>100 ms), paused, invalid or conflicting samples. Never mix ARCore origins across resumed sessions. If a fit is rejected, retain unaligned VGGT output and report why. Preserve the job's tracking inputs and fit metrics. ARCore scale remains an estimate; keep geometry labeled unscaled until a known measurement validates it.

This is external pose alignment, not feeding IMU into VGGT's neural inference or dense SLAM fusion. A good fit cannot repair wrong local depth. Physical tracking, synchronization, preview proportions and output quality must each be validated separately.


## Accumulated tracked map and point counts (2026-10-18)

Remove the 500,000-point model-export cap and 100,000-point desktop payload cap. Stream jobs override legacy saved profile caps with zero (uncapped); new profiles and CLI defaults are uncapped. Preserve valid confidence-filtered output rather than fabricate points. Spatially deduplicate aligned observations into 0.01 ARCore-unit cells; the number of cells is not capped and grows with observed space. Preserve independent snapshot geometry and provenance, merge camera indices consistently, and never combine resumed sessions with unrelated origins. The UI distinguishes accumulated tracked batches from unaligned estimates. Memory/disk use still grows with map size; no finite machine provides infinite points. VGGT input count and GPU batch latency remain separate limits, explicitly recorded rather than represented as continuous dense SLAM.

Use ARCore Frame.getAndroidCameraTimestamp() as the pose timestamp, retaining the internal AR timestamp separately. The encoder may need the documented uptime-to-REALTIME conversion described below. Preserve raw PTS and explicit clock provenance; physical synchronization is still a separate acceptance gate.


Room switching: the capture screen exposes **New room**, which stops/drains the current video and returns to room naming. **Start new room** always creates a separate scan, even when another scan is unfinished. The home screen lists unfinished rooms with **Resume this room**. Preserve pending uploads with their original scan IDs and scope visible counts to the current room. New-room capture does not require eight frames or successful reconstruction in the previous room.


The desktop offers Set floor: select three non-collinear floor points to level the view and show an unscaled grid. Floor selection is explicit, not automatic plane detection. Accepted ARCore alignment supplies gravity-up but does not establish floor height. Preview rotation is a display-only control preserving aspect ratio.


Phone floor and orientation: Set floor on camera arms a touch selection. Only an ARCore-tracked upward-facing horizontal plane hit inside its polygon can establish a floor anchor; the user identifies the actual floor rather than a table. Draw an estimated grid on the phone and send the floor pose with subsequent per-frame metadata. A new ARCore session needs a fresh floor selection. Supply sensor-to-display rotation for portrait playback without stretching or modifying raw reconstruction pixels. Surface actual outgoing encoder fps. Phone selection appears in the desktop cloud only after accepted alignment.

Frame viewpoints: the desktop shows timestamp-validated ARCore camera centers, trajectory and selected/all camera frusta with exact source frame IDs, position estimates, sensor types and tracking failure states. A frame selector and video playback time select the corresponding viewpoint. Invalid/missing poses are not fabricated. Unregistered sensor paths appear separately from image-estimated geometry, never overlaid as though aligned. Coordinates are relative to one ARCore origin; they are not GPS positions or measurement-validated dimensions.


Timestamp correction prepared after inspecting saved physical sessions: Android video encoder DEFAULT output can use uptime while REALTIME camera metadata uses elapsedRealtime. Native matching now considers the measured elapsedRealtimeNanos-to-uptimeMillis conversion only for REALTIME sensors, retaining raw encoder PTS, conversion offset and clock-source labels. Choose direct camera-clock matching when it is closer, and retain the server 100 ms rejection gate. This supersedes the failed assumption that getAndroidCameraTimestamp alone shares the encoder clock. The updated clock APK was built after phone disconnection and is NOT installed/physically verified yet. See https://developer.android.com/reference/android/hardware/camera2/params/OutputConfiguration#TIMESTAMP_BASE_DEFAULT and https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_INFO_TIMESTAMP_SOURCE .
