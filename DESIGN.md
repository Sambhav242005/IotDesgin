# IoTDesign design

## Architecture

### Reconstruction showcase and diagnostic isolation

The loopback `reconstruction-timeline` endpoint joins durable frames with frozen job inputs and completed metrics. The viewer requests historical status, geometry, cameras and downloads using the same completed job ID, rejecting stale asynchronous responses. Its frame strip windows thumbnails while retaining all frame identities. Source picture ordinals map to FFmpeg's generated 30 fps HLS media timeline; recorded capture elapsed time is separate and never used as that media clock. Sensor trajectories require timestamp acceptance and accepted same-origin alignment before overlaying cloud geometry.

`colmap_validation.py` lazily imports optional PyCOLMAP and runs CPU feature extraction, exhaustive matching and incremental mapping in a new per-run directory. It copies hash-verified originals and tracking metadata into a diagnostic snapshot, writes no phone priors into COLMAP, and preserves disconnected model summaries rather than merging unrelated origins. Comparison converts ARCore OpenGL axes to OpenCV and fits one similarity per origin after the 100 ms clock gate. Coverage, motion, per-frame residuals and image-support checks qualify the verdict. Artifacts stay under `scans/<scan>/colmap/<run>/`; no production scan/job state is changed.

The native Expo/React Native app owns continuous Android camera video capture/encoding and retains a durable upload queue for legacy photo scans. A Windows FastAPI process owns HTTPS, pairing, mDNS advertisement, SQLite, and image/artifact access. A separate Windows CUDA worker claims reconstruction jobs from the same database and writes artifacts to the same data directory.

```mermaid
flowchart LR
    Phone[Native mobile app] -->|Discovery| MDNS[Windows mDNS advertisement]
    Phone -->|Pinned HTTPS: pairing and continuous H.264| API[FastAPI server]
    API --> DB[(SQLite)]
    API --> Files[Images and artifacts]
    Worker[Single Windows CUDA worker] --> DB
    Worker --> Files
    Worker --> VGGT[Official VGGT]
    Desktop[Loopback desktop station] --> API
    Viewer[Saved-scene viewer] --> Files
```

The original plan proposed WSL2 Ubuntu for the worker. The current implementation uses Windows-native CUDA, including an `msvcrt` lock. This avoids introducing shared-filesystem and networking assumptions into the validated path. A WSL2 worker requires a deliberate portability and storage migration.

## Connection and trust

The server advertises `_iotdesign._tcp.local.` with identity, API-version, port, and certificate information. The app checks a saved address while discovering for eight seconds. A remembered stable server ID takes priority; a single new candidate enters pairing, and multiple candidates require selection. Manual address entry provides a fallback.

Health checks establish compatibility and compare the reported fingerprint with the TLS peer fingerprint. First-time manual pairing requires comparison with the desktop fingerprint. The QR route carries pairing information and pins the supplied fingerprint. An eight-digit expiring pairing code is exchanged for a credential. Subsequent requests authenticate with that credential and pin the saved certificate.

Server identity/address and credentials are persisted through SecureStore. Address changes can be recovered using the stable ID after authenticated reconnection. A changed certificate requires renewed trust. App foreground/network events trigger connection checks. Desktop pairing-code endpoints are restricted to loopback.

## Capture and upload

Android stream scans mount `VideoCameraView`. `ArCameraTracking` owns an ARCore SHARED_CAMERA session, GL preview renderer and timestamped IMU capture. Camera2 recording requests include ARCore's surfaces and a single app surface for the hardware encoder. SharedCamera wraps device/session callbacks; ARCore resumes after the capture session becomes active. ARCore's transformed texture coordinates account for the portrait viewfinder's rotation and aspect ratio. MediaCodec still receives the camera directly at 1280x720, 30 fps, H.264 baseline / 2.5 Mbit/s with one-second keyframes, without audio. Failure to initialize tracking is surfaced; it does not silently masquerade as tracked capture.

The encoder assigns ordered video-frame indices to non-configuration output buffers. Before each selected access unit is written, it sends the closest ARCore physical-camera pose, AR timestamp, encoder PTS and bounded IMU samples to `/tracking` using the same pinned HTTPS identity. The persistent video POST includes `X-Video-Session`; source and tracking filenames join through that session ID and frame index. The worker freezes the available per-frame tracking files into each job's artifacts and passes them to reconstruction. A 100 ms matching tolerance is a rejection threshold, not a precision claim; device synchronization still requires evidence.

`VideoReceiver` incrementally parses and decodes H.264 with PyAV. It writes all incoming bytes to a unique source file before decoding, preserving partial sessions after failure. It derives desktop preview JPEGs and reconstruction views on the PC, preserving every decoded frame for consecutive GPU batches. That internal model selection does not reduce video transport to timed images. Model views retain source-session/frame-index provenance in their IDs; source receipts record received bytes, decoded count and completion. Video decoding is CPU work outside the request event loop. The desktop embeds player.html: FFmpeg remuxes the encoded stream into retained fMP4 HLS segments without re-encoding, and HLS.js/native HLS plays and seeks those recordings. JPEG previews remain for legacy callers.

Pause/backgrounding/unmount closes camera capture and signals encoder EOS. Finish explicitly awaits encoder drain, HTTP completion and the PC receipt before freezing; the server rejects Finish while a video request is active. Resume opens a fresh codec/session, not an append to an undecodable stream. A transport failure is surfaced and the user can Pause/Resume; video captured during a disconnected interval is not queued or recovered. This differs from the still-photo queue and must remain explicit. Received video and prior model views remain preserved.

Legacy photo scans and iOS keep the original durable JPEG queue, idempotent frame endpoint and separate still capture. The `capturePreview` helper remains only for legacy photo preview. Reconnect preserves active capture state. The scheduler and worker continue to use frozen, durable PC-extracted JPEG views.

## Persistence and jobs

SQLite stores server metadata, paired clients, scans, frames, jobs, and worker heartbeat. Image and artifact bytes live in files rather than database blobs.

Photo scans begin as `capturing`, become `frozen` when reconstruction is queued, and become `completed` after successful export. Stream scans remain `capturing` across GPU snapshots and freeze only when explicitly finished. Jobs move through `queued`, `running`, and `completed` or `failed`. A failed job may leave its scan frozen; job state is the authoritative failure indicator. Reconstruction freezes the input set so uploads cannot silently alter a queued job.

One worker holds the OS lock and claims one job at a time. On worker startup, interrupted running jobs are marked failed with preserved source images. The desktop retry action queues another attempt. A heartbeat distinguishes a healthy worker from a stale one. Benchmarks share the worker lock, so stop the worker before running a benchmark against its data directory.

## Reconstruction and point count

Official VGGT estimates camera poses and depth from evenly selected source images. Original JPEGs are preserved while model inputs are resized separately. The current worker default is a 336-pixel input width aligned to the model's patch size. Camera/depth inference uses CUDA mixed precision.

Export keeps finite, positive-depth points whose confidence is at or above the median. No default fixed point-count cap is applied after validity/confidence filtering. An optional explicit photo-worker CLI cap remains available; stream jobs override saved legacy profile caps with zero (uncapped). A reconstruction memory failure retries once with eight images when the initial selection exceeds eight. Benchmark runs disable that retry to preserve the requested 8/12/20 comparison.

Artifacts include colored `cloud.ply`, estimated `cameras.json`, `preview.jpg`, `viewer.npz`, and a versioned `scene.json`. Metrics include duration, input count/resolution, peak allocated VRAM, sampled process RAM, and exported point count. The preview is a source-image preview. The representation remains unscaled; real distances require a known measurement.

## Viewing and user experience

Each dashboard scan links to a loopback-only captured-photo gallery (`photos.html`). It lists frame metadata from SQLite, requests 320-pixel thumbnails lazily, and opens the preserved source JPEG in a new tab. It polls for newly acknowledged uploads without replacing existing thumbnails. The mobile pending thumbnails currently become checkmarks after acknowledgment and local queued-file deletion; originals remain on the PC. The gallery covers both capturing and completed scans.

With `worker --segment-objects`, official SAM 2.1 Hiera Small runs after VGGT releases its model. It segments the selected images independently, one image at a time, at a maximum 1024-pixel edge. A 16 x 16 prompt grid proposes regions; quality filtering and a 64-mask per-frame limit bound storage. Overlapping masks remain separate, stored as packed bits in `objects.npz`. `objects.json` records frame IDs, processed/source dimensions, boxes, scores, and per-frame IDs. Colored overlays are provided in `objects-preview.jpg` and `objects-previews.zip`. The scene manifest links these artifacts, which retain the API's ownership checks.

Segmentation is opt-in through the CLI and enabled by Windows launcher's GPU profiles. `--semantic-objects` runs YOLO OpenImages Nano before SAM 2, releasing detector memory before segmentation. YOLO proposes category names and internal prompt boxes; SAM 2 supplies masks. IDs remain frame-local; moving and reflective classifications remain unknown. Masks can contain parts, overlapping objects or background surfaces. Detection does not guarantee that every mirror is found.

VGGT exports per-point frame indices and normalized original-image coordinates through resize/crop/padding and export filtering. These associate masks with surviving geometry. The desktop review page displays masks/contours and explicit exclusion controls. Revisions reject stale edits. `viewer-base.npz` and `cloud-unfiltered.ply` preserve base geometry for restoration without reconstructing. Association supplies approximate unscaled centroids where sufficient points exist; Mirror and Window positions remain withheld. The viewer can color points by proposed object and reload reviewed geometry. Automatic motion/material inference and measured placement remain separate future stages.

The preview displays colored mask regions and their contours, without bounding boxes. Bounding extents remain metadata for downstream processing, not the displayed representation.

The desktop station presents pairing, worker availability, scans, and reconstruction status. The saved-scene Viser viewer supports navigation and camera visualization. A separate Three.js HTML showcase loads the saved Pexels benchmark cloud and provides movement/orbit and zoom controls. Its file server is separate from the authenticated phone API.

The mobile flow progresses through discovery/manual connection, pairing, connected home, and capture. Connection errors should explain the action needed: permission access, network reachability, API compatibility, or renewed authentication. Capture exposes pause/resume, manual capture, thumbnails, upload counts, and finish.

## Validation limits and future work

Mirror prompts retain a box-only fallback and compare it with three point-prompt candidates plus one logits-based refinement. Nine interior positive points and valid exterior negative points are generated in resized-image coordinates. A bounded quality/coverage/leakage ranking chooses one proposal; metadata records the strategy and heuristic points. This improves one observed reflected-content failure but can regress elsewhere. Non-mirror detections and the generic automatic-mask generator retain their previous paths.

The semantic path now adds CPU Grounding DINO Tiny prompted with `mirror.` between YOLO and SAM 2. Grounded boxes use the same resized-image coordinates as SAM prompts; same-category duplicates are suppressed. CUDA custom kernels are disabled for this Windows path. Scores are category proposals, not reflective-material certainty. The [grounded-mirror report](docs/research/grounded-mirror-validation.md) records poor mask accuracy despite improved candidate recall. There is no VLM scene-description pass.

For semantic masks, mirrors/glass, moving objects, and measured IoT placement, consult the [three-agent research synthesis](docs/research/README.md). Semantic proposals, image masks, provenance and explicit review are implemented. Motion/material inference, persistent tracks and measured placement remain research stages.

The Windows launcher probes CUDA FP16 execution and free memory, selects a conservative profile, caches selected models and starts hidden services. Its separate launcher lock serializes starts while the worker keeps its GPU lock. Server identity prevents reuse of unrelated installations; PID creation time and command/data-directory checks constrain stop operations. Low stores the VGGT aggregator in FP16 while retaining FP32 geometry heads. The [quantization report](docs/research/windows-low-memory-quantization.md) records backend limitations and measured resource use.

The Android emulator has run the native app, paired manually through its host gateway, and uploaded 30 virtual-camera frames. This verifies a simulated capture/transport path; it does not establish real-phone mDNS reliability or room reconstruction quality.

The Pexels 8/12/20 benchmark completed, but its nearly fixed camera and moving people produced poor geometry. Real-room multi-view validation remains open. Generated OpenAPI-based mobile types, WSL2 worker support, iPhone validation, and VGGT-Gaussian evaluation remain future work. RF simulation, measured placement geometry, persistent AR, and human motion reconstruction require separate designs.


Live scan selection uses the server status list of unexpired previews belonging to capturing scans with valid paired clients. Preserve the current selection while it is live; otherwise follow a currently streaming scan. Empty newer scans must not hide an active older feed. Preview failures are shown with their specific error rather than a generic reconnect message.

## Live reconstruction snapshots

`stream_sessions` records stream-mode scans, the number of originals covered by the last queued snapshot, and whether input has stopped. `job_inputs` freezes ordered frame IDs for each stream job. The stream scheduler runs within immediate SQLite transactions on durable upload, stream finish, and successful worker completion. The existing unique active-job index coalesces updates. Eight saved frames start inference; four new frames trigger the next snapshot. Completed work schedules a waiting update even if no new request arrives afterward. Finish schedules a final partial update and marks the scan completed only after all acknowledged input has been covered by successful work. Explicit retries create a fresh snapshot; interrupted jobs remain failed and recoverable.

The scheduler freezes consecutive eight-view windows, advances four new frames and retains all backlog. All supported profiles must process the complete frozen window. Stream retries cannot reduce input count; failed windows block progress and explicit retry copies their precise job_inputs. VGGT reads only the persisted snapshot IDs. Originals are never replaced or pruned to make a moving window. Video stream scans retain every PC-decoded picture plus source video, within the existing per-connection encoded-byte/time limits. The 30-item local JPEG queue applies only to legacy photos. `mobile/src/stream.ts` handles legacy preview file lifetime. Finish waits for native video shutdown and PC receipt, then any legacy queue draining before freezing. SecureStore writes remain serialized.

Progressive jobs omit optional segmentation. Unaligned outputs remain independent unscaled estimates; accepted same-origin tracking alignments accumulate into the live map. The loopback `scene.html` viewer polls job status, loads a completed-job-bound XYZRGB Float32 payload, and replaces/disposes the prior point cloud. It retains the last completed cloud through subsequent inference or failure. Its binary payload and renderer receive every exported point; full PLY artifacts remain available. Page visibility and container rendering visibility pause background work. Three.js currently loads from the same pinned CDN version as the existing showcase.


## Tracking alignment

`camera_tracking.py` converts ARCore column-major OpenGL physical-camera transforms to OpenCV camera axes, groups samples by session origin and fits one scale/rotation/translation using image-estimated and tracked camera poses. Camera rotations constrain global rotation; camera centers constrain scale and translation. Accepted fits transform points and camera exports together. Fewer than three matches, insufficient movement, stale/paused tracking, invalid rotations, large position residuals or conflicting orientations leave the image-only geometry unchanged and produce an explicit status. The manifest remains unscaled until measurement validation. Keep source pose snapshots and alignment residuals for review. This does not correct per-frame nonlinear depth errors or provide a registered dense SLAM map.


Historical clock attempt (superseded): a generic System.nanoTime suspend-offset guess failed on the Samsung device. Direct Android-camera timestamps also remained about 26 hours from encoder PTS. The documented encoder-uptime to REALTIME sensor conversion below is the next prepared correction; physical verification remains pending.


## Accumulated map and final timestamp contract

The generic suspend-offset timestamp attempt did not match this Samsung device. Use ARCore's documented `Frame.getAndroidCameraTimestamp()` for matching MediaCodec PTS directly, retain its separate `Frame.getTimestamp()` for provenance, and continue enforcing the 100 ms camera-clock gate.

`live_map.py` accumulates accepted ARCore-aligned batches into the previous completed map for the same scan and ARCore session. Preserve each independent model snapshot alongside the map. Merge provenance and offset camera indices, then deduplicate occupied 0.01 ARCore-unit cells. There is no total cell/point-count cap. A new session origin starts a separate map; rejected alignment does not get merged. Every exported map point is sent to the desktop, without the previous 100,000-point sample. This remains GPU-batch accumulation; it is not a video-rate incremental depth estimator or dense SLAM.


Room switching: the capture screen exposes **New room**, which stops/drains the current video and returns to room naming. **Start new room** always creates a separate scan, even when another scan is unfinished. The home screen lists unfinished rooms with **Resume this room**. Preserve pending uploads with their original scan IDs and scope visible counts to the current room. New-room capture does not require eight frames or successful reconstruction in the previous room.


Viewer floor calibration stores three-point-derived plane origin/normal in browser local storage, scoped by scan and accepted ARCore session (or individual unaligned job). Apply it after display normalization; retain it across same-origin maps, never transfer it to unrelated estimates. Reject close/collinear picks, allow cancel/flip/clear, and pause cloud replacement during selection. Downloaded geometry is unchanged. Recreate orbit controls when camera up changes. Landscape encoder preview remains uncropped with optional 90-degree display rotation.


The every-frame path supersedes historical every-60th selection. Snapshot coverage counts advance only through the consecutive frozen window, not all arrived frames. Received/processed/pending counts distinguish ingestion from inference throughput. SQLite orders frames through a scan/timestamp index. include_frames=false provides counts without sending an ever-growing frame list back to the phone.

playback.py runs one hidden FFmpeg remuxer per incoming source session. It copies H.264 into fMP4 HLS, atomically publishes completed fragments and retains the complete segment list for rewind. Loopback routes whitelist init.mp4, index.m3u8 and numbered fragments. state.json carries portrait rotation and playback errors. Resume creates a separate recording; the player exposes sessions rather than concealing capture gaps. player.html uses custom controls outside the rotated video element so portrait controls stay upright.

The native floor anchor is created only from a tracked horizontal upward plane hit. Its pose renders a grid through the camera view/projection matrices and accompanies synchronized samples. Only accepted ARCore alignment carries that floor into reconstructed coordinates. Camera-path pagination exposes each frame's pose, timing gate and sensor-type counts. The viewer renders the trajectory and selected camera frustum; playback/frame selection synchronizes by session and frame index. A missing alignment shows the sensor trajectory independently of the VGGT cloud.


Timestamp correction prepared after inspecting saved physical sessions: Android video encoder DEFAULT output can use uptime while REALTIME camera metadata uses elapsedRealtime. Native matching now considers the measured elapsedRealtimeNanos-to-uptimeMillis conversion only for REALTIME sensors, retaining raw encoder PTS, conversion offset and clock-source labels. Choose direct camera-clock matching when it is closer, and retain the server 100 ms rejection gate. This supersedes the failed assumption that getAndroidCameraTimestamp alone shares the encoder clock. The updated clock APK was built after phone disconnection and is NOT installed/physically verified yet. See https://developer.android.com/reference/android/hardware/camera2/params/OutputConfiguration#TIMESTAMP_BASE_DEFAULT and https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_INFO_TIMESTAMP_SOURCE .
