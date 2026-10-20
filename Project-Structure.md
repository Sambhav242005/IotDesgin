# Project structure

Reconstruction replay is implemented in `scene.html` and `player.html`, with read-only frame/batch history assembled by `server/iotdesign/timeline.py` and loopback routes in `api.py`. `tests/test_timeline.py` covers historical job binding, access and separate capture/media clocks. Optional image-only COLMAP diagnostics live in `server/iotdesign/colmap_validation.py`; `cli.py` exposes `colmap-check`, and `tests/test_colmap_validation.py` checks evidence gates. Per-run local artifacts are preserved under `data/scans/<scan>/colmap/<run>/`.

## Source and documentation

```text
IotDesign/
|-- AGENTS.md                 Repository development instructions
|-- Project-Structure.md      This source map
|-- DESIGN.md                 Architecture and implementation decisions
|-- SPEC.md                   Requirements and acceptance status
|-- README.md                 Installation and operation
|-- GATES.md                  Recorded validation gates
|-- MISTAKES.md               Incorrect JPEG interpretation and corrective evidence
|-- DECISIONS.md              Continuous H.264 transport decision and limits
|-- pyproject.toml            Python dependencies, CLI, and tool configuration
|-- server/iotdesign/
|   |-- cli.py                serve, worker, view, benchmark, doctor, openapi
|   |-- config.py             Data directory, network settings, and limits
|   |-- api.py                Mobile API and loopback desktop endpoints
|   |-- models.py             API request and response models
|   |-- db.py                 SQLite schema and persistence helpers
|   |-- security.py           Certificates, credential hashing, pairing codes
|   |-- discovery.py          Windows-host mDNS advertisement
|   |-- worker.py             Job claiming, heartbeat, recovery, GPU lock
|   |-- reconstruction.py     VGGT inference, filtering, export, metrics
|   |-- segmentation.py       Optional official SAM 2 image-region masks
|   |-- viewer.py             Saved-scene Viser viewer
|   `-- desktop.html          Pairing station and scan/job dashboard
|-- mobile/
|   |-- AGENTS.md             Mobile-specific development instructions
|   |-- App.tsx               Connection, pairing, capture, and upload UI
|   |-- index.ts              Native app entry
|   |-- app.json              Expo configuration and platform permissions
|   |-- package.json          Dependencies and scripts
|   |-- src/protocol.ts       Address, QR, API-version, discovery decisions
|   |-- tests/                Protocol tests
|   `-- modules/iotdesign-network/
|       |-- src/index.ts      Typed native interface
|       |-- android/src/      Kotlin NSD and pinned HTTPS implementation
|       `-- ios/              Swift Bonjour and pinned HTTPS implementation
|-- tests/test_api.py         API and job-recovery tests
|-- tests/test_segmentation.py Mask export and overlap round-trip tests
|-- scripts/setup-sam2.ps1    Install the pinned optional SAM 2 source
`-- docs/
    |-- openapi.json          Exported API schema
    |-- research/             Object names, mirrors/glass, motion, and IoT placement reports
    `-- benchmarks/
        |-- pexels-7578017.md          Benchmark results and limitations
        `-- pexels-7578017-viewer.html Three.js sample showcase
```

## Local and generated files

Windows entry point: `Start-IoTDesign.cmd` invokes `scripts/start-windows.ps1`. `hardware.py` detects resources and selects a profile; `setup_models.py` caches selected checkpoints; `launcher.py` starts/stops verified background services. Bootstrap files are ignored under `tools/`.

Object understanding: `recognition.py` proposes labels, `segmentation.py` creates masks, `object_geometry.py` maps masks to preserved point provenance and applies reversible review edits, and `review.html` displays mask review. Hardware/launcher tests are in `tests/test_hardware.py`; low-memory findings are in `docs/research/windows-low-memory-quantization.md`.

`server/iotdesign/live.py` holds bounded, expiring in-memory phone previews. `api.py` exposes owned preview uploads and loopback reads; `desktop.html` displays the feed. The Android network module's `capturePreview` reads the CameraX camera surface without capturing other UI. `tests/test_live.py` covers expiration and memory bounds; API tests cover preview trust boundaries.

`server/iotdesign/photos.html` is the captured-photo gallery; its loopback metadata, thumbnail, and original-image routes are in `api.py` and covered by API tests.

`data/` holds the SQLite database, server certificate material, uploaded images, reconstruction artifacts, and benchmark media/results. Its default database is `data/iotdesign.sqlite3`. A job exports to `data/scans/<scan-id>/artifacts/<job-id>/`.

`vendor/vggt/` is the local official VGGT checkout. Downloaded checkpoint storage is managed by the model-download library. `.venv/` and `mobile/node_modules/` contain dependencies.

`mobile/android/` and `mobile/ios/` are generated native projects. Configure their behavior through Expo configuration and plugins according to `mobile/AGENTS.md`. The local debug APK is generated at `mobile/android/app/build/outputs/apk/debug/app-debug.apk`.

Runtime data, vendored models, dependency directories, and generated native projects are ignored by Git. Native-module build directories are also generated outputs; consult `.gitignore` before adding files.

## Change routing

`server/iotdesign/prompts.py` builds bounded mirror point prompts, compares candidates and refines from low-resolution logits. `tests/test_prompts.py` checks image-edge behavior, ranking, fallback and refinement inputs.

The semantic recognition stage also uses CPU Grounding DINO Tiny for targeted mirror proposals. `scripts/validate-mirror-grounding.py` compares resulting masks against three local Mirror3D example ground-truth masks; `docs/research/grounded-mirror-validation.md` records the limitations.

| Change | Primary files |
|---|---|
| Discovery and connection decisions | `mobile/App.tsx`, `mobile/src/protocol.ts`, native network module, `server/iotdesign/discovery.py` |
| Pairing and transport trust | Native network module, `security.py`, `api.py` |
| Capture and resumable uploads | `mobile/App.tsx`, frame-upload route in `api.py` |
| Persistence or recovery | `db.py`, `worker.py`, API tests |
| Reconstruction and point density | `reconstruction.py` |
| Desktop scan dashboard | `desktop.html`, loopback routes in `api.py` |
| Saved-scene viewing | `viewer.py`; sample showcase in `docs/benchmarks/` |

See [DESIGN.md](DESIGN.md) for data flow and [SPEC.md](SPEC.md) for expected behavior.

Continuous Android video: `mobile/modules/iotdesign-network/android/src/main/java/expo/modules/iotdesignnetwork/VideoCameraView.kt` owns Camera2 capture, MediaCodec H.264 encoding and pinned chunked HTTPS video upload. `src/VideoCamera.tsx` exports that native view. `server/iotdesign/video.py` preserves/decodes incoming video and extracts PC-side model views. `tests/test_video.py` checks real H.264 parsing, preservation, scheduling and trust boundaries.

Progressive reconstruction: `server/iotdesign/streaming.py` schedules immutable snapshots stored in `stream_sessions`/`job_inputs`; `worker.py` reads those inputs and schedules subsequent updates. `scene.html` is the loopback auto-refreshing 3D viewer. `mobile/src/stream.ts` now controls only legacy photo-preview file lifetime. `tests/test_streaming.py` covers job isolation, coalescing, finish and recovery; `mobile/tests/stream.test.mjs` covers legacy persistence/transport/cleanup ordering. `scripts/validate-live-stream.py` exercises GPU snapshots from an existing JPEG fixture; it does not validate video transport.


Visual-inertial tracking: native `ArCameraTracking.kt` shares ARCore with video capture, renders the correctly transformed preview and collects timestamped rotation/gyro/acceleration. `/api/scans/{id}/tracking` stores immutable owned metadata. `server/iotdesign/camera_tracking.py` validates and aligns camera/point geometry; `worker.py` freezes per-job metadata. `tests/test_tracking_api.py` covers transport/ownership/snapshot identity and `tests/test_camera_tracking.py` covers alignment, stale/paused/conflicting poses and separate origins.


Accumulated tracked map: `server/iotdesign/live_map.py` merges accepted same-origin batches, retains snapshots/provenance and spatially deduplicates without a fixed point cap. `tests/test_live_map.py` covers accumulation, origin separation and full desktop payload above the former render cap.


Browser video playback: playback.py copies H.264 to retained fMP4/HLS with FFmpeg; player.html supplies playback/rewind/session/portrait controls. API playback and camera-path routes are loopback-only. scene.html visualizes per-frame ARCore camera trajectory/frusta and source frame IDs; ArCameraTracking.kt anchors the phone-selected floor. tests/test_playback.py checks live segment publication, complete decoded-frame coverage and desktop access boundaries.


scene/cameras returns job-bound image-estimated camera poses; camera-path returns paginated sensor measurements with timestamp acceptance, frame IDs and summarized sensor types. player.html and scene.html exchange same-origin frame/session messages for viewpoint/video scrubbing.
