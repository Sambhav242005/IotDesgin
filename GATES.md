# Gates: Phone to PC room reconstruction

## 2026-10-20 — reconstruction replay and COLMAP phone-pose diagnostic

- Added loopback source-frame/batch history, job-bound historical metrics/points/cameras/downloads, source video beside the cloud, frame state thumbnails, selectable source cameras/image planes, and replay of completed batches. Missing historical observations are not synthesized.
- Capture elapsed time and generated 30 fps remux media time are separate; the latter maps decoded picture ordinal to video playback and is not sensor evidence.
- Optional PyCOLMAP 4.2.1 CPU baseline freezes/hash-verifies originals, reconstructs without phone priors, and applies geometry, coverage, clock, origin, motion and per-frame residual/inlier checks before a consistency verdict. Reports are atomic; original scans and production jobs are preserved.
- Server suite: 74 tests passed; Ruff server/tests passed. Lead separately ran 16 timeline/COLMAP tests and all eight `node --test tests/desktop-replay.test.mjs` regression tests, including shipped-script syntax, source/job selection, stale responses, missing historical ordinals, renderer failure, diagnostics and cross-session seek. The real saved scan's timeline, historical status/cameras/points/download routes and latest report identity passed. CLI help and generated OpenAPI were checked; eight updated documents had no missing local Markdown links.
- Actual final CPU run: 43 saved historical images, 27 registered cameras, 674 sparse points, 46.922 seconds, native process exit 0. Zero synchronized phone pairs; about 26.07 hours of clock mismatch among active registered records. Verdict inconclusive (62.79% registered coverage below 80%). See [diagnostic evidence](docs/benchmarks/colmap-phone-pose-20261020.md).
- Physical phone acceptance is pending. The in-app browser refused the local certificate with `ERR_CERT_AUTHORITY_INVALID`; no warning was bypassed. Automated API/script evidence does not establish visual correctness, floor anchoring, improved room geometry, accurate phone matrices or sustained 30 Hz reconstruction.

OWNS: server/**, mobile/**, tests/**, scripts/**, docs/**, pyproject.toml, README.md

Scope: native capture, discovery and remembered pairing; durable API and GPU worker; saved scene viewer.

- [x] G1: API tests verify authentication, idempotency, validation, freezing and job recovery
  CHECK: .venv\Scripts\python.exe -m pytest -q
  EXPECT: passed
  EVIDENCE: 6 passed; `ruff check server tests` passed
- [x] G2: Mobile types and connection lifecycle tests pass
  CHECK: npm.cmd test
  EXPECT: mobile checks passed
  CWD: mobile
  EVIDENCE: 4 tests passed; `npm.cmd run typecheck` passed; Android bundle exported
- [x] G3: Android development APK compiles with discovery and pinned transport
  EVIDENCE: Gradle `app:assembleDebug` succeeded; local Kotlin discovery/HTTPS module compiled; APK at `mobile/android/app/build/outputs/apk/debug/app-debug.apk`
- [ ] G4: Real phone discovery, pairing, camera capture and reconnect work on Wi-Fi
  EVIDENCE: pending
- [ ] G5: Real room reconstruction and 8/12/20-frame GPU benchmark complete
  EVIDENCE: The 8/12/20 benchmark ran on Pexels clip 7578017, but its camera is nearly fixed and it does not meet the real-room capture gate; see `docs/benchmarks/pexels-7578017.md`.
- [x] G6: Saved point cloud and cameras load in the desktop viewer
  EVIDENCE: The 20-frame saved scene loaded in the local Viser viewer with 500,000 points and 20 camera poses. It revealed poor geometry for this near-static clip.
- [ ] G7: iPhone native module compiles and discovery works on a device
  EVIDENCE: pending

- [x] G8: Official SAM 2 image segmentation runs on the local GPU and exports reloadable masks
  EVIDENCE: 12 Pexels frames produced 128 region masks in 6.746 seconds at 736.02 MiB peak allocated CUDA memory; see `docs/benchmarks/pexels-7578017-sam2.md`. Subsequent semantic run produced 47 named proposals. Mask IDs remain per frame; motion/material inference remains pending. Explicit review exclusions and restoration are implemented; YOLO missed mirrors in three Mirror3D samples.

- [x] G9: Windows profile selection, existing-environment setup and hidden launcher work
  EVIDENCE: 22 tests pass, including competing GPU lock acquisition; Ruff passes. PlanOnly and Low SetupOnly ran on this Windows host. Hidden server/Low worker started and stopped with a data path containing spaces. Existing installation/services were reused. Fresh-machine bootstrap and physical GTX 1050 Ti remain unvalidated.
- [x] G10: Reduced-memory VGGT and SAM 2 Tiny execute within the configured allocation budget
  EVIDENCE: On RTX 4070 Ti with 3.2 GiB allocator limit, VGGT 8x168x112 completed in 7.26s at 2888.55 MiB allocated VRAM / 9824.73 MiB process RAM. Sequential YOLO/SAM 2 Tiny generated 13 regions at 525.21 MiB VRAM. See quantization report. This is not a physical 1050 Ti benchmark.

- [x] G11: Grounding DINO mirror proposals feed SAM 2 with proposal provenance
  EVIDENCE: 25 tests pass. Real Windows CPU DINO inference and SAM 2 Tiny/Small ran on three Mirror3D images, proposing mirrors on all three. Tiny mask IoUs 0.3500/0.0012/0.2112 remain poor, and Small did not resolve the boundary failures; reliable mirror handling is an open gate. See grounded-mirror report.

- [x] G12: Bounded mirror point prompts and mask refinement run with fallback and provenance
  EVIDENCE: 29 tests pass. Three-sample mean IoU improved Tiny 0.1875→0.3915 and Small 0.1871→0.3641. Sample 45 regressed; no broad accuracy guarantee is established. CUDA peaks remained 525.21/551.79 MiB. See grounded-mirror report for individual results and heuristic limitations.


## 2026-10-16: Android live PC preview

- Automated validation: 33 server tests passed; Ruff passed. Four mobile protocol tests, TypeScript checking, and Expo lint passed. Native ARM64 debug build succeeded and was installed on the connected Samsung SM-N971N. Lint used a temporary generated config; no lint/dependency configuration changes are retained. OpenAPI was re-exported.
- Physical phone evidence: resumed a saved scan, observed the rear-camera preview and `Live camera ? PC`, and observed selected-photo upload count increasing. Server logs recorded repeated authenticated preview uploads and durable frame uploads returning HTTP 200. Reconnect checks no longer returned the active camera to home during this observation. This does not establish real-room reconstruction quality or sustained streaming performance.
- The preview targets up to five updates per second, silently, at a maximum 640-pixel edge; actual cadence depends on still capture and network overhead. Preview continues after the 30 selected-photo limit until paused, backgrounded, or finished. iOS preview remains unimplemented.
- Ownership, missing authentication, invalid/oversized previews, frozen scans, revocation, loopback-only viewing, expiry and cache bounds are covered by tests. Physical reconnect/interruption, prolonged thermal/battery testing, and desktop visual confirmation remain pending. Browser security policy blocked automated access to the local HTTPS dashboard; the user must refresh that page to load the new panel.


## 2026-10-16: Captured-photo gallery

- The latest physical-phone scan had 30 preserved JPEG frame records. Added a loopback-only gallery with saved-photo counts, lazy 320-pixel thumbnails, original JPEG links, and polling for new uploads. Phone acknowledged thumbnails still become checkmarks; originals remain on the PC.
- Validation: 34 server tests passed; Ruff passed; OpenAPI re-exported. New tests cover gallery metadata, original bytes, thumbnail decoding, missing frames/scans, HTML escaping and rejection of remote gallery/image requests. No mobile changes in this gallery update. Desktop visual confirmation remains pending because automated browser access was blocked by browser security policy in this session.


## 2026-10-16: Live-feed code diagnosis

- Reproduced a dashboard selection defect without a phone: an empty newer unfinished scan stayed selected while an older scan streamed. The regression executes the shipped dashboard script against a minimal DOM; it failed before the fix and passed afterward. The dashboard now prefers a currently live scan and preserves a live selection. Server status omits expired, frozen or revoked streams. Mobile preview errors are no longer swallowed.
- Validation: 34 server tests, six mobile/dashboard tests, TypeScript checking and Ruff passed. Device end-to-end confirmation remains pending because the phone was disconnected; the user requested code diagnosis first. The existing implementation remains sampled JPEG preview rather than encoded video.

- Explicit ESLint checking of root App.tsx and the dashboard regression test passed after moving render-time ref updates into effects and matching the camera-permission callback dependency. Earlier default Expo lint did not establish coverage of the root App.tsx file. No native Android code changed in this diagnosis; no reinstall is required for the dashboard fix.


## 2026-10-18: Progressive reconstruction from the Android live feed

- New Android scans use `capture_mode: stream`. An exact sampled camera-feed JPEG is copied into the durable upload queue approximately every two seconds; separate timed still capture is disabled for stream scans. At eight uploaded frames the server queues a frozen-input GPU snapshot while leaving the scan capturing. Four new frames request subsequent snapshots, coalesced behind one active job. Finish drains in-flight frame handling/uploads, freezes input and schedules any final partial update. The server retains originals and prior job outputs. Existing photo scans remain supported.
- Automated validation: **39 server tests passed**; **nine mobile/dashboard/stream tests passed**; TypeScript checking, explicit ESLint checks of `App.tsx`, `src/stream.ts` and mobile tests, and Ruff (`server tests scripts/validate-live-stream.py`) passed. ESLint used a temporary generated flat config that was removed afterward. Android JS/Hermes export succeeded into ignored runtime storage. OpenAPI was re-exported. Relative links in the root documentation were checked.
- Real GPU fixture replay: `scripts/validate-live-stream.py` completed two successive snapshots on RTX 4070 Ti using Pexels frames. Eight frames produced 263,424 points (14.36 s VGGT metric / 19.52 s worker-to-browser-payload wall time; 5,104.72 MiB peak allocated VRAM). Twelve frames produced 395,136 points (11.77 s / 12.48 s; 5,147.98 MiB). Frames arriving after the first job was claimed did not change that job's eight frozen inputs, and the next update was scheduled automatically on completion. Final stream closure reached completed. Details: [stream validation report](docs/benchmarks/live-stream-20261018.md).
- Browser evidence: the loopback live viewer loaded the real completed cloud with 395,136 saved points (100,000 rendered sample points), and the reset control was exercised. Visual screenshot inspected and stored at ignored `data/benchmark/live-stream-20261018/viewer.png`. Backend tests cover completed-job-bound payloads, remote denial, scan ownership, final partial batches, coalescing, recovery/retry and limit-safe acknowledgment retries. Browser replacement without navigation was then observed: the displayed snapshot changed from `2f2cf6b3` to `039a7f6a` after republishing the existing real-GPU fixture through the worker. This extra browser check reused geometry and did not run fresh inference; `browser-refresh.json` records it. The real GPU replay separately verifies successive new reconstructions and server publications.
- Production HTTPS server and High-profile worker were started through the installation launcher; health reported ready. A hidden LAN Metro server was started on port 8081 and its status endpoint reported running. ADB listed no attached device. No native module code was changed for progressive reconstruction; the existing Android `capturePreview` implementation is reused. Physical phone streaming, actual room quality, network interruption and sustained thermal/battery behavior remain unvalidated.
- Limits: current source is sampled JPEG camera-surface frames at a maximum 640-pixel edge, not encoded full-rate video. Progressive batches skip optional segmentation. Snapshots replace the prior unscaled cloud and have independently estimated coordinates; they do not establish registered/fused SLAM mapping. The viewer currently requires WebGL and its pinned Three.js CDN. The stock fixture has the previously documented poor viewpoint baseline and does not close G4/G5.
- Documentation consulted: [Expo SDK 57 legacy filesystem](https://docs.expo.dev/versions/v57.0.0/sdk/filesystem-legacy/) and Context7 Expo/Three.js references. Modern Web Guidance's `efficient-background-processing` informed hidden-page and offscreen-container rendering/polling suspension, with a direct container event listener and an IntersectionObserver fallback.


## 2026-10-18 ? corrected continuous H.264 transport

The earlier sampled-JPEG implementation did not satisfy the user's request for continuous video to the PC. It is superseded for Android stream scans. See [MISTAKES.md](MISTAKES.md) and [DECISIONS.md](DECISIONS.md).

- Camera2 repeating video capture feeds preview and MediaCodec input surfaces directly. Requested encoder format: H.264 baseline, 1280x720, 30 fps, 2.5 Mbit/s, no audio. Every encoded output buffer travels over a persistent pinned/authenticated HTTPS request. No JS still-photo timer drives Android stream scans.
- PyAV 17.1.0 installed; `av>=16,<18` added to runtime dependencies. The PC preserves received H.264, incrementally decodes, creates preview JPEGs and selects reconstruction views. Partial source files are retained on interruption.
- Server: `python -m pytest -q` ? **42 passed**; `python -m ruff check server tests` ? passed. Tests encode real inter-frame H.264, decode across 137-byte boundaries, prove source-byte preservation and scheduling before EOF, and check auth/owner/content-type/frozen/error cleanup. The continuous decoder guard and receipt validity were tightened after the physical session; final source tests include those changes.
- Mobile: **9 tests passed**, TypeScript passed, explicit ESLint of App/native TS interfaces passed. Native `:app:assembleDebug -PreactNativeArchitectures=arm64-v8a` passed; development APK installed successfully on connected Samsung Android device with `adb install -r`. No generated native source was hand-edited.
- User reported continuous-video status and increasing sent KiB. A probe of the received source decoded H.264 at **1280x720**. The PC file grew while GPU jobs ran.
- Physical runtime scan `b9df19a0-2993-4d9a-9de1-5072e7f1708d`: finalized video receipt recorded **48,638,950 bytes, 3,919 decoded video frames, 66 extracted reconstruction views**, and `complete: true`. Original source is retained in ignored runtime storage. This scan already had **five legacy JPEG inputs** from before the updated APK; do not treat it as a clean video-only quality benchmark.
- The scan reached `completed`; all **16 jobs** completed. Recent outputs used official VGGT on RTX 4070 Ti, 12 inputs at 336?336, **500,000 points**, around **7.45?7.50 s** inference. This verifies physical video delivery and video-driven GPU updates, not room geometry accuracy or real-time registered fusion.
- User reported Pause/Resume/Finish success. Server evidence confirms successful final EOF/receipt and completion for **one** video source session; a second independent resume source file was not observed, so reconnect/resume-session acceptance remains separate.
- Existing source images, credentials, pinned certificate and prior artifacts were preserved. Video bytes and receipts remain under local runtime storage, outside Git.

Remaining acceptance: clean video-only room scan and visual geometry assessment; independently evidenced Pause/Resume reconnect sessions; forced Wi-Fi loss/recovery; thermal/long-duration cadence; alternate Android camera hardware. There is no offline video queue or automatic stream retry. Desktop display remains PC-decoded JPEG previews; source transport/archive is continuous H.264. Progressive point clouds remain independent unscaled VGGT batches. iOS video and registered SLAM are pending.

Docs consulted: installed Expo 57 native-module source and matching SDK docs, Expo native-view API, Android Camera2/MediaCodec reference, and PyAV raw H.264 parser/decode documentation (links in DECISIONS.md). OpenAPI was re-exported; local documentation links checked.


## 2026-10-18 ? ARCore camera tracking and preview transform

- Added ARCore 1.57.0 shared-camera capture with physical camera pose estimation and timestamped rotation vector, gyroscope, accelerometer and linear acceleration. Requested video remains continuous H.264; the preview now renders ARCore-transformed texture UVs rather than stretching the landscape sensor surface into a portrait viewfinder.
- Added authenticated, owned, bounded, idempotent tracking metadata; native session/frame identity joins metadata to PC-decoded inputs. Worker jobs retain their tracking inputs. Added conservative pose alignment with axis conversion, per-origin grouping, timestamp/movement/residual/rotation checks and explicit rejection states. Unscaled labeling remains until a physical measurement validates scale.
- **47 server tests passed**; Ruff passed. Tests cover known-transform point/camera alignment, stale/paused/static/conflicting poses, separate ARCore origins, metadata auth/owner/idempotency and video-to-worker identity. **9 mobile tests**, TypeScript and explicit ESLint passed. Arm64 native development APK built successfully; generated platform sources were not edited.
- Initial physical startup attempt failed: device log showed `camera_device was passed NULL` followed by a JNI abort in `SharedCamera.getArCoreSurfaces`. Cause: internal surfaces were fetched before CameraDevice.onOpened. Corrected by deferring surface acquisition to the wrapped device-open callback, matching Google's official shared-camera lifecycle. Corrected build passed; physical retest required before reporting tracking operational.
- Device: Samsung SM-N971N with Google Play Services for AR 1.57.262640093 installed. Presence of the package does not by itself establish successful tracking or synchronization.

The previous distorted scan remains preserved. Input inspection found legacy upright JPEGs mixed with raw sideways H.264 views and many near-static repeated views. Phone visual-inertial data was not captured for that scan and cannot be recovered retrospectively. External pose alignment is not IMU input to VGGT inference, dense SLAM fusion, a correction of nonlinear depth errors or evidence of room geometry accuracy.


Physical retest after the shared-camera callback fix: scan `965fd299-a0c7-4c76-98b1-caf9f283f396` received continuous video and tracking metadata. At one inspection there were 18 TRACKING samples, two PAUSED samples and one initializing sample; the PC retained rotation vector (type 11), gyroscope (4), accelerometer (1) and linear acceleration (10), with 2,042 readings of each type. Native startup no longer aborted.

Synchronization exposed a device clock mismatch: encoder PTS was around 244,147,423,273,000 ns while ARCore/sensor timestamps were around 338,002,413,371,835 ns. The initial alignment correctly reported unavailable with zero matches rather than fitting unrelated times. The corrected native build normalizes an encoder monotonic timestamp with the measured elapsed-realtime minus monotonic suspend offset when that clock matches ARCore, preserves the original encoder timestamp and labels the normalized clock. Pose timing still must pass the 100 ms gate. Corrected build passed; clock-normalized physical retest and successful alignment remain to be recorded.


## 2026-10-18 ? uncapped output and accumulated tracked batches

Removed default 500,000-point export and 100,000-point display limits. New worker CLI/profile defaults use zero (uncapped); stream jobs override legacy saved profile caps. Validity/confidence filters still apply, and finite GPU input resolution still bounds each depth estimate. The binary desktop endpoint returns every exported point.

Added same-origin accumulation of accepted ARCore-aligned batches with 0.01 ARCore-unit spatial deduplication and no fixed total-point limit. Independent snapshots, point provenance and camera references remain preserved. Different ARCore origins are not merged. Updates still occur at VGGT batch latency; this does not establish video-rate dense SLAM or repair nonlinear depth errors.

Validation: **50 server tests passed** and Ruff passed. New tests verify known-pose alignment, camera-clock matching despite a different internal ARCore clock, accumulated geometry/provenance, independent origins and full binary payload above 100,000 points. Mobile: prior 9 tests, typecheck and explicit ESLint passed; corrected arm64 APK built/installed successfully. Documentation and scene module syntax checked. Server/GPU worker restarted with the updated implementation.

The generic clock-offset attempt remained mismatched in scan `924a713d-a13d-465c-bd33-343948ec5f02`; tracking data was correctly rejected. The final timestamp contract now uses ARCore Frame.getAndroidCameraTimestamp() to match encoder PTS directly, preserving Frame.getTimestamp() separately. Latest native build installed; physical synchronization/alignment/accumulation acceptance remains pending until measured. No successful physical pose alignment or improved room quality is claimed solely from the automated tests.


Room-switching correction: added capture New room, unrestricted Start new room and explicit per-room Resume controls. Previous scans and pending uploads retain their original IDs; active-room counts ignore acknowledgments for old-room uploads. Mobile typecheck, nine existing tests and explicit ESLint passed. These existing tests do not establish physical room-switching acceptance; device validation remains pending.


2026-10-18 floor viewer: added explicit three-point plane selection, unscaled grid, flip/clear, coordinate-scoped local persistence and aspect-preserving preview rotation. Latest real scan e8736f69 / snapshot fb4b9688 still reports unavailable tracking with zero matched cameras; do not claim verified world pose or automatic floor detection. Browser interaction and physical floor accuracy remain unverified.
Validation for the floor-viewer change: 50 server tests passed; Ruff passed; extracted viewer JavaScript passed node --check; git diff --check passed. These checks do not validate WebGL picking, browser storage, or physical floor accuracy. The HTML is read per request, so refreshing the viewer loads the change without restarting capture.


2026-10-18 all-frame/playback/floor/path update: synthetic encoded-video tests verify complete frame coverage through consecutive reconstruction batches, exact failed-window retries, all-picture fMP4 playback before EOF, portrait metadata, batched per-frame tracking, and loopback-only recording/trajectory access. Mobile floor anchor rendering, actual portrait output, browser rewind controls and sensor-path physical accuracy still require real-device/browser acceptance. No 30 Hz dense reconstruction claim. The earlier 32.23 fps ingestion timing predates adding HLS remux and excludes GPU inference; it is not evidence of end-to-end 30 fps.


Timestamp correction prepared after inspecting saved physical sessions: Android video encoder DEFAULT output can use uptime while REALTIME camera metadata uses elapsedRealtime. Native matching now considers the measured elapsedRealtimeNanos-to-uptimeMillis conversion only for REALTIME sensors, retaining raw encoder PTS, conversion offset and clock-source labels. Choose direct camera-clock matching when it is closer, and retain the server 100 ms rejection gate. This supersedes the failed assumption that getAndroidCameraTimestamp alone shares the encoder clock. The updated clock APK was built after phone disconnection and is NOT installed/physically verified yet. See https://developer.android.com/reference/android/hardware/camera2/params/OutputConfiguration#TIMESTAMP_BASE_DEFAULT and https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_INFO_TIMESTAMP_SOURCE .


Final checks for this update: 58 server tests passed; Ruff passed; 9 mobile tests and TypeScript passed; explicit ESLint passed for modified TS/TSX; all shipped desktop scripts passed node --check; native Gradle reported BUILD SUCCESSFUL. Five preserved sessions were remuxed on the PC, with 3,919/3,919 decoded pictures verified for the open historical scan. Video/floor/portrait native build installed before disconnect; the subsequent clock-conversion APK is prepared but not installed. Browser interactions and physical orientation/floor/camera synchronization remain pending.
