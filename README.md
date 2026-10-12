# IoTDesign: phone capture to PC reconstruction

IoTDesign captures overlapping room photos on an Android phone, uploads them to a Windows PC on the same Wi-Fi network, and reconstructs a colored point cloud with VGGT. The PC capture station shows scan and GPU worker status; completed scenes open in a local interactive viewer. Coordinates are unscaled.

## Start the PC server

GPU profiles include a local CPU Grounding DINO `mirror.` pass before SAM 2. Its masks are review proposals; it can miss mirrors or segment reflected contents incorrectly. See [targeted mirror validation](docs/research/grounded-mirror-validation.md).

Windows x64: double-click **`Start-IoTDesign.cmd`**. It installs a managed Python environment, dependencies and pinned official model sources, downloads selected checkpoints, detects usable GPU/system memory, and starts the HTTPS server and one GPU worker in the background. Internet and several GB of disk space are required for first setup. An NVIDIA driver must already be installed; the script does not install drivers or reboot Windows.

| Automatic profile | Inputs | VGGT storage | Segmentation | Export cap |
|---|---|---|---|---|
| High: compatible 10+ GB GPU / 24+ GB RAM | 12 frames, 336 px width | FP32 with mixed-precision inference | SAM 2 Small | 500,000 |
| Balanced: compatible 6+ GB GPU / 14+ GB RAM | 8 frames, 224 px width | FP16 aggregator / FP32 heads | SAM 2 Small | 300,000 |
| Low: compatible 4 GB GPU / 12+ GB RAM | 8 frames, 168 px width | FP16 aggregator / FP32 heads | SAM 2 Tiny | 150,000 |
| ServerOnly: unavailable CUDA or insufficient current free memory | Capture/upload only | No local worker | None | None |

The GTX 1050 Ti / 16 GB laptop targets Low if at least 11 GiB RAM and 3.1 GiB VRAM are currently free and the CUDA smoke test succeeds. Low passed a 3.2 GiB allocation-budget test on the RTX 4070 Ti; physical GTX 1050 Ti validation is pending. Lower resolution loses detail. The point cap controls export size rather than inference memory. Close other applications before starting. See [quantization findings and measurements](docs/research/windows-low-memory-quantization.md).

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

Photos are captured about every two seconds, resized to a maximum 1280-pixel edge, and saved in app storage before upload. The app retries uploads on reconnection; frame IDs make a repeated upload safe. Capture at least eight overlapping views; 12–20 clear views are a better first room scan. Keep the camera moving slowly and avoid blank walls, motion blur, or major lighting changes.

To build an iPhone version, use macOS with Xcode and run `npx expo prebuild --platform ios` followed by `npx expo run:ios`. iOS uses Bonjour and asks the user to allow local-network access. An iPhone device build has not been validated on this Windows host.

## View a completed scene

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
