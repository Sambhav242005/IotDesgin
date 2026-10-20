# IoTDesign: phone capture to PC reconstruction

IoTDesign streams the Android camera to a Windows PC on the same Wi-Fi network, preserves continuous H.264 video and selects decoded views on the PC, and updates a colored VGGT point cloud as GPU batches complete. The PC capture station shows scan and worker status and opens the auto-refreshing live 3D viewer. Existing photo scans retain their finish-then-reconstruct workflow. Coordinates are unscaled.

## Start the PC server

The scan's 3D view includes side-by-side source video, source-frame thumbnails, selectable camera markers and completed-batch replay. Select a thumbnail or camera to inspect its source image; use Previous/Next batch, Replay batches and Follow latest to switch saved reconstruction results. Unaligned batches remain independent estimates.

For an independent image-only check of recorded phone camera matrices, install the optional local backend and run from the repository root:

```powershell
uv pip install --python .venv/Scripts/python.exe pycolmap==4.2.1
.\.venv\Scripts\python.exe -m iotdesign.cli --data data colmap-check --scan <scan-id>
```

This CPU diagnostic uses all saved images by default. `--max-frames N` explicitly limits only the diagnostic sample. It writes hash-verified inputs, sparse cameras/PLY and a JSON report under `data/scans/<scan-id>/colmap/<run-id>/`; the latest report appears in the viewer's COLMAP diagnostics. The check compares camera-pose consistency, not raw IMU accuracy or measured dimensions. Bad clocks, inadequate motion, weak geometry or low coverage produce an inconclusive/rejected report. Production VGGT reconstruction and originals are preserved. See [COLMAP documentation](https://colmap.github.io/) for the upstream pipeline.

GPU profiles include a local CPU Grounding DINO `mirror.` pass before SAM 2. Its masks are review proposals; it can miss mirrors or segment reflected contents incorrectly. See [targeted mirror validation](docs/research/grounded-mirror-validation.md).

Mirror segmentation compares box-only, positive/negative point prompts, and one previous-mask refinement. Automatically chosen points are proposals; they do not establish the true mirror boundary. The three-sample check improved average overlap but also showed a regression, so review remains necessary.

Windows x64: double-click **`Start-IoTDesign.cmd`**. It installs a managed Python environment, dependencies and pinned official model sources, downloads selected checkpoints, detects usable GPU/system memory, and starts the HTTPS server and one GPU worker in the background. Internet and several GB of disk space are required for first setup. An NVIDIA driver must already be installed; the script does not install drivers or reboot Windows.

| Automatic profile | Inputs | VGGT storage | Segmentation | Export cap |
|---|---|---|---|---|
| High: compatible 10+ GB GPU / 24+ GB RAM | 12 frames, 336 px width | FP32 with mixed-precision inference | SAM 2 Small | Uncapped |
| Balanced: compatible 6+ GB GPU / 14+ GB RAM | 8 frames, 224 px width | FP16 aggregator / FP32 heads | SAM 2 Small | Uncapped |
| Low: compatible 4 GB GPU / 12+ GB RAM | 8 frames, 168 px width | FP16 aggregator / FP32 heads | SAM 2 Tiny | Uncapped |
| ServerOnly: unavailable CUDA or insufficient current free memory | Capture/upload only | No local worker | None | None |

The GTX 1050 Ti / 16 GB laptop targets Low if at least 11 GiB RAM and 3.1 GiB VRAM are currently free and the CUDA smoke test succeeds. Low passed a 3.2 GiB allocation-budget test on the RTX 4070 Ti; physical GTX 1050 Ti validation is pending. Lower resolution loses detail. Confidence filtering still applies; export point count is uncapped by default. Stream batches use eight consecutive views across profiles. Close other applications before starting. See [quantization findings and measurements](docs/research/windows-low-memory-quantization.md).

```powershell
.\Start-IoTDesign.cmd -PlanOnly
.\Start-IoTDesign.cmd -Profile Low -SetupOnly
.\Start-IoTDesign.cmd -NoBrowser
.\Start-IoTDesign.cmd -Stop
```

`-Stop` stops only verified launcher-owned processes. Existing services are reused; an existing worker retains its settings until stopped. Logs and detected settings are under `data/logs/` and `data/hardware-profile.json`. Run `-PrivateFirewall` from an elevated window to add Private-network HTTPS and mDNS rules, or use the instructions below. Model caches are reused. The launcher does not build/install the mobile app.

Manual setup remains available:

Use Windows Python 3.10–3.13. From PowerShell in this directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
iotdesign serve --name "My IoTDesign PC"
```

Open `https://127.0.0.1:8443` on the PC. The first run creates a local TLS certificate and a pairing code. The desktop page shows a QR code, the current code, and the SHA-256 certificate fingerprint. The phone app verifies that fingerprint before it sends the pairing code. Discovery advertises the PC as `_iotdesign._tcp.local.`.

If Windows Firewall blocks discovery or uploads, allow the server port and mDNS on a private network from an elevated PowerShell window:

```powershell
New-NetFirewallRule -DisplayName "IoTDesign HTTPS" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "IoTDesign mDNS" -Direction Inbound -Protocol UDP -LocalPort 5353 -Action Allow -Profile Private
```

The phone and PC must be on the same network, and the Wi-Fi router must allow devices to communicate with each other. Guest networks and access-point isolation can block both discovery and uploads.

## Start the GPU worker

The server accepts scans without the GPU worker, but reconstruction remains queued. Keep the worker in a second PowerShell window on the same Windows PC so it uses the same SQLite database and image files:

```powershell
.\.venv\Scripts\Activate.ps1
iotdesign doctor
iotdesign worker --repo vendor/vggt --resolution 336
```

Install a CUDA-enabled PyTorch build that matches the installed NVIDIA driver before starting the worker. Then install the project GPU dependencies with `python -m pip install -e ".[gpu]"`. The worker uses official VGGT and downloads its model checkpoint on first run. `iotdesign doctor` reports whether the GPU runtime and VGGT checkout are ready. Keep at least 20 representative JPEGs in one folder to run the 8/12/20-frame benchmark:

```powershell
iotdesign benchmark --images path\to\room-images --repo vendor/vggt --resolution 336
```

The benchmark writes per-size results and resource metrics under `data/benchmark`. Gaussian splat reconstruction remains a later experiment; this prototype exports a point cloud, camera poses, preview, and scene manifest.

## Build and install the Android app

The app includes a local native module for Android NSD discovery and certificate-pinned HTTPS, so Expo Go is not sufficient. Install Node.js and Android Studio with an Android SDK, then:

```powershell
cd mobile
npm install
npx expo prebuild --platform android
npx expo run:android
```

The prebuild creates the ignored native Android project from Expo configuration. The app requests local-network and camera permissions as needed. On its first launch, it searches for the PC for eight seconds. If it finds one PC, enter the code from the PC station and compare the displayed certificate fingerprint. If it finds multiple PCs, select one. The app stores the PC address and pairing token securely, checks the saved PC on later launches, and uses discovery to recover when its address changes. If discovery fails, enter the PC address shown by the desktop station. A scanned pairing QR verifies the certificate and completes pairing directly.

Android stream scans continuously transmit H.264 video to the PC, which preserves the encoded source and decodes reconstruction views. Existing photo scans and iOS keep their original durable JPEG queue. Move slowly through overlapping views; avoid blur, blank walls and major lighting changes.

While Android video capture runs, **Live phone camera** shows PC-decoded previews; the underlying phone-to-PC transport is continuous H.264. Pause/backgrounding stops video. Resume opens a new video session. The preview clears after three seconds without fresh decoded data. iOS video is pending. A native rebuild/reinstall is required; Metro reload alone is insufficient.

To build an iPhone version, use macOS with Xcode and run `npx expo prebuild --platform ios` followed by `npx expo run:ios`. iOS uses Bonjour and asks the user to allow local-network access. An iPhone device build has not been validated on this Windows host.

## View a completed scene

To see the captured photos before or after reconstruction, click **View captured photos** under the scan on the PC dashboard. The number shows how many photos are saved. Select a thumbnail to open the original JPEG. The gallery updates as new photos arrive. Phone thumbnails currently become checkmarks after successful upload; the PC retains those images.

The PC capture station lists scans, job state, and reconstruction metrics. Open a completed point cloud with:

```powershell
iotdesign view --scan <scan-id>
```

The viewer runs at `http://127.0.0.1:8080` and supports orbit, pan, zoom, camera positions, reset, and artifact downloads. The point cloud is for visual inspection; measure a known distance before using it for physical layout or RF estimates.

## Object segmentation with Meta SAM 2

SAM 2 is an optional image-region segmentation stage. Install the pinned official source into the existing CUDA Python environment:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-sam2.ps1
```

The installer skips the optional custom CUDA extension on Windows. The official SAM 2.1 Hiera Small checkpoint downloads from Meta's Hugging Face repository on first use. Meta recommends WSL for Windows installations; this project's image inference path has been tested natively with postprocessing disabled. The source/checkpoint license is Apache 2.0; preserve the upstream notices in `vendor/sam2`.

Stop the existing GPU worker before running a standalone segmentation command: they share the data-directory lock.

```powershell
.\.venv\Scripts\python.exe -m iotdesign.cli segment --images path\to\images --output data\segmentation-demo --count 12
```

For new reconstruction jobs, restart the worker with segmentation enabled:

```powershell
.\.venv\Scripts\python.exe -m iotdesign.cli worker --repo vendor/vggt --resolution 336 --segment-objects
```

VGGT completes and frees GPU model memory before SAM 2 runs. Exports include `objects.json`, packed overlapping masks in `objects.npz`, a first-frame `objects-preview.jpg`, and all selected-frame overlays in `objects-previews.zip`. Completed scene manifests link the artifacts, and the saved-scene viewer provides downloads. The standalone command also writes these files without running VGGT.

Masks use resized-image coordinates with original dimensions recorded. To decode a frame's masks, load the metadata's `mask_array` from the NPZ and apply `numpy.unpackbits(..., axis=1, count=height*width, bitorder='little')`, then reshape to `(mask_count, height, width)`.

SAM 2 proposes boundaries, including object parts and background regions. IDs are local to each frame. This stage does not assign names such as person/chair/mirror, determine movement or reflectivity, track identity, or remove points from the 3D cloud. See [DESIGN.md](DESIGN.md) and [SPEC.md](SPEC.md).

## Development checks

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

From `mobile/`:

```powershell
npm test
npm run typecheck
npx expo-modules-autolinking resolve --platform android
npx expo-modules-autolinking resolve --platform apple
```

Discovery and camera capture need physical-device testing on a private Wi-Fi network. A native build needs the Android SDK. An iPhone build needs macOS and Xcode.

## Reconstruct while streaming (Android)

Restart the server and GPU worker after updating, then reload the Android development app. The existing native `capturePreview` module is reused, so this change does not itself require a native rebuild; an older APK without that module still needs the development build described above. Start a **new** room scan; old saved photo scans keep their original mode. On the PC dashboard, select **Open live 3D** under that scan.

Move slowly through overlapping views. After eight PC-decoded views, the first GPU batch starts automatically. Four additional frames request the next update; the GPU handles one batch at a time and combines pending requests. You do not have to press Finish to see reconstruction. The source is continuous hardware-encoded H.264 over one pinned HTTPS connection. The PC selects model views after decoding; no timed phone JPEG uploads drive this path. Accepted tracked batches accumulate within one ARCore origin; unaligned results remain independent estimates. This is not a video-rate fused SLAM map. Geometry remains unscaled. The viewer requires WebGL and internet access to its pinned Three.js CDN.

Pause closes video capture and upload. Resume opens a new video session within the scan. Finish waits for encoder drain and the PC video receipt before freezing and completing the last update. All PC-decoded video pictures and received source video are retained; the legacy JPEG-upload limit does not cap video frames. Each video connection is limited to one hour / two GiB. Network failures create gaps; Pause/Resume retries with a fresh stream. There is no offline video queue. Optional object segmentation is skipped for progressive batches. Continuous H.264 from the physical Samsung phone and successful finalization have been verified; clean video-only room quality and reconnect/long-duration acceptance remain pending. See GATES.md.

Transport correction and its rationale: [MISTAKES.md](MISTAKES.md), [DECISIONS.md](DECISIONS.md).


## Phone camera tracking

The updated Android native build uses ARCore visual-inertial tracking alongside continuous H.264. Start a new scan and move the phone slowly through a textured, well-lit scene. The status reports ARCore tracking state; lost/initializing tracking is not a reliable pose. Rotation, gyro and acceleration samples are preserved with synchronized camera metadata. The PC applies camera-pose alignment only when timing, movement and consistency checks pass. The viewer reports the result. Existing saved scenes have no motion metadata and are not retroactively corrected. ARCore support and a native rebuild/install are required; this is not an Expo Go feature.

Tracking does not guarantee correct dense geometry. The preview uses ARCore rotation/aspect transforms. Point clouds remain unscaled pending measurement validation, and batch reconstruction is not a fused SLAM map. See SPEC.md and GATES.md for validation status.


Fixed export and display point caps are removed. All valid filtered output points are exported and displayed. Accepted synchronized ARCore-aligned batches accumulate into one map for that tracking origin; repeated spatial observations are deduplicated, without a fixed point-count limit. VGGT still uses selected image batches internally and updates at GPU batch latency. This does not yet provide video-rate dense SLAM; unaligned outputs are independent estimates. Historical saved scenes remain unchanged.


Room switching: the capture screen exposes **New room**, which stops/drains the current video and returns to room naming. **Start new room** always creates a separate scan, even when another scan is unfinished. The home screen lists unfinished rooms with **Resume this room**. Preserve pending uploads with their original scan IDs and scope visible counts to the current room. New-room capture does not require eight frames or successful reconstruction in the previous room.


Live video controls: open a scan's 3D viewer or `/desktop/scans/<scan-id>/player` for Play/Pause, Back 10 s, a rewind slider, session selection and Go live. Playback requires FFmpeg on the PC PATH (or IOTDESIGN_FFMPEG) and native HLS or access to pinned HLS.js 1.7.3. Segments retain all video pictures; a short playback buffer is expected. Android carries portrait rotation without stretching. The stream UI shows video status and measured fps, rather than a row of photo checkmarks.

On the phone, start streaming, tap **Set floor on camera**, then tap the visible floor after moving slowly enough for ARCore to track its plane. An anchored grid confirms selection; a horizontal table is also a plane, so select the actual floor. Each resumed tracking session requires a fresh floor. Frame viewpoints on the PC show the sensor-derived camera trajectory, source IDs, timing validity and position/orientation. Accepted alignment overlays it with the cloud; otherwise the path is a separate estimate.

Every decoded picture enters consecutive overlapping reconstruction batches. The viewer reports processed/received/pending frames. This can build a backlog: all-frame input coverage does not establish 30 reconstructed scenes per second. Physical playback/portrait/floor/path acceptance for the latest APK remains pending.
