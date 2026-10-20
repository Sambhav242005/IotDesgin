# Read first: project reminders and next tasks

Last updated: 2026-10-20, Asia/Calcutta.

This file preserves the working context for a fresh session. It is a handoff, not proof that a feature works or that services are still running. Follow the user's newest instructions and the repository's other instructions.

## User requirements to remember

- Stream continuous encoded camera video from the Android phone to the PC. Do not substitute periodically captured photos and call that video streaming.
- Use every received/decoded video frame for reconstruction, targeting the phone's 30 fps stream. Explain separately whether GPU processing can keep up; all-frame coverage does not establish 30 reconstructed scenes per second.
- Grow the point cloud without an arbitrary total-point cap. Retain validity/confidence filtering and report resource or processing limits honestly.
- Use camera observations and phone motion sensors for position/orientation. Show where each frame came from using a camera trajectory, viewing directions, source frame IDs and timing evidence.
- Provide video playback with pause, rewind, seeking and Go live, like a streaming player.
- Set the floor on the mobile camera view and show an anchored grid. Do not make PC-only floor selection the primary workflow.
- Respect portrait capture and preserve proportions instead of stretching or presenting portrait footage sideways.
- Allow Start new room even if another room is unfinished; offer explicit Resume this room separately.
- Keep MISTAKES.md, DECISIONS.md and acceptance records accurate as behavior changes.

## Priority tasks: continue here

- The user requested the source-frame/camera/cloud showcase and an independent COLMAP check of phone camera matrices. Implementation now includes side-by-side video/cloud, frame states, source camera/image selection, historical completed-batch replay and a COLMAP report in the viewer. Keep actual UI acceptance separate: the in-app browser was blocked by `ERR_CERT_AUTHORITY_INVALID`; no security warning was bypassed.
- Optional local PyCOLMAP 4.2.1 is installed. `colmap-check --scan <id>` preserves frozen inputs and uses image-only CPU reconstruction, strict clock matching, separate origins, geometry/coverage/inlier gates and atomic reports. Final saved historical run: 27/43 cameras, 674 points, zero synchronized phone pairs, approximately 26.07-hour clock mismatch; verdict inconclusive. See `docs/benchmarks/colmap-phone-pose-20261020.md`. This does not validate or disprove the phone's spatial matrices. A fresh corrected-clock capture remains necessary.
- Raw H.264 browser playback uses generated 30 fps media timestamps. Viewer source-picture ordinal/media time and recorded capture elapsed time are separate fields. Do not use encoder elapsed time to seek this remux or use media time as sensor evidence.

1. **Install and physically validate the newest clock-conversion APK when the phone reconnects.** The phone was disconnected at the user's request. The earlier video/floor/portrait build installed successfully; the subsequent clock-conversion APK was built afterward and has NOT been installed or physically accepted. Build output: `mobile/android/app/build/outputs/apk/debug/app-debug.apk`.
2. **Verify frame-to-pose timing before claiming sensor-based reconstruction works.** Saved physical sessions showed approximately a 26-hour encoder/camera clock mismatch, including sessions using `getAndroidCameraTimestamp()`. The prepared correction considers encoder uptime versus REALTIME sensor elapsed time, preserving raw PTS and clock provenance. Keep the 100 ms rejection gate. Confirm matched frames and accepted alignment from new device evidence; do not rewrite old metadata or invent positions.
3. **Physically check mobile floor selection and portrait output.** Start a new scan, choose Set floor on camera, tap the actual tracked floor and verify that the grid stays anchored. Confirm upright, proportional phone preview and PC playback. Each new ARCore origin requires a fresh floor selection. A horizontal table is not automatically the floor.
4. **Validate the PC player and per-frame camera visualization interactively.** Check play/pause, rewind, session selection, Go live, frame scrubbing, pose IDs and viewing directions. Confirm timing failures are visible and sensor paths never appear registered to an unaligned cloud. Prior browser automation encountered an access/certificate-policy restriction: respect tool access checks and do not bypass a denial.
5. **Measure actual all-frame throughput and reconstruction quality.** Every decoded picture now queues for consecutive overlapping GPU batches. Inspect received/processed/pending counts and backlog growth during a real stream. Test accepted same-origin map accumulation and recognizable room geometry. GPU batch inference is not established as 30 Hz dense SLAM.

While the phone is disconnected, continue useful PC-side development and verification. Leave physical acceptance explicitly pending; do not repeatedly request the same device check or pretend it has passed.

## Implemented at the last handoff

- Android Camera2/MediaCodec sends continuous H.264 through pinned, authenticated HTTPS. Per-picture tracking is bundled six frames at a time before corresponding video bytes.
- PC video ingestion preserves encoded source and every decoded picture. The scheduler uses consecutive windows of at most eight views, advancing four new frames with overlap. Failed batches block further progress until their exact frozen inputs are retried; short final tails are drained.
- Stream jobs disable point-count caps and frame-dropping inference retries. Accepted same-origin tracked batches accumulate with spatial deduplication. Unaligned estimates and different ARCore origins are not merged.
- FFmpeg copies H.264 into retained fMP4/HLS segments. `player.html` provides video playback and custom controls outside the rotated video element. Native HLS or pinned HLS.js is required; missing FFmpeg/playback support reports an error.
- Native ARCore floor selection anchors a grid on a user-selected, tracked upward-facing horizontal plane. Floor metadata reaches cloud coordinates only after accepted alignment.
- `scene.html` shows orange image-estimated cameras, blue sensor trajectories and a selected green camera frustum. Source IDs, positions, sensor types and clock-match status distinguish image estimates from usable sensor evidence. Video and viewpoint selection exchange same-origin frame/session messages.
- Portrait rotation metadata is carried separately from original reconstruction pixels. Historical recordings without orientation metadata have a manual Rotate video control.
- Mobile stream UI removes the photo-checkmark row and shows video status plus measured outgoing encoder fps. Summary polling avoids repeatedly downloading every frame record.

## Last validation evidence and its limits

- 2026-10-20: 74 server tests and Ruff passed; lead separately verified 16 timeline/COLMAP tests and eight desktop replay JavaScript regressions including syntax. Actual saved-scan timeline, historical geometry/camera/download endpoints and latest COLMAP report identity passed. Browser interaction remains blocked by the local certificate warning, and no physical phone validation was performed.

- 58 server tests passed; Ruff passed.
- 9 mobile tests, TypeScript and explicit ESLint passed.
- Shipped desktop scripts passed `node --check`; native Gradle reported BUILD SUCCESSFUL.
- Five preserved source sessions were remuxed for playback. The historical open scan's playback decoded all 3,919 pictures, matching its source receipt.
- These checks do NOT establish physical floor accuracy, correct portrait output on the newest capture, successful sensor synchronization, browser interaction correctness, improved room geometry or sustained 30 Hz reconstruction.
- An earlier 32.23 fps ingestion measurement predates HLS remux and excludes GPU inference. Do not cite it as end-to-end 30 fps performance.

## Resume pointers

- Read SPEC.md, DESIGN.md, Project-Structure.md and mobile/AGENTS.md as required by AGENTS.md. GATES.md records chronological acceptance evidence; newer corrections supersede earlier assumptions.
- Video and playback: `server/iotdesign/video.py`, `playback.py`, `player.html`.
- Scheduling and processing: `streaming.py`, `worker.py`, `reconstruction.py`, `live_map.py`.
- Sensor matching and viewer: `camera_tracking.py`, `api.py`, `scene.html`.
- Native camera, orientation, floor and clocks: `mobile/modules/iotdesign-network/android/src/main/java/expo/modules/iotdesignnetwork/ArCameraTracking.kt` and `VideoCameraView.kt`.
- The browser was open to historical scan `b9df19a0-2993-4d9a-9de1-5072e7f1708d`. That recording lacks usable sensor poses; refreshing it cannot create missing physical tracking data. New-capture validation needs the new scan/session.
- On 2026-10-20 the old server/worker were no longer running. The verified launcher started the HTTPS server on port 8443 for viewing saved scenes; the GPU worker was not restarted. COLMAP diagnostics ran on CPU and exited successfully. Recheck runtime state and ownership; do not reuse remembered PIDs. Launcher metadata is local runtime data under `data/`.
- On 2026-10-20 the user authorized committing and pushing the accumulated source, tests and documentation. Inspect Git status/log for the current publication state; preserve any later unrelated changes. Generated architecture snapshots, local routing records, builds and runtime media/credentials are excluded from version control.
- Keep credentials, certificates, pairing codes and captured media out of this reminder file.
