# Implementation decisions

## 2026-10-20 — independent COLMAP evidence and reconstruction replay

Keep VGGT as the production backend and add optional image-only CPU PyCOLMAP diagnostics without phone priors. Preserve frozen originals, identities, hashes and phone records in a separate per-run directory. Report disconnected reconstructions and require geometric support, registered coverage, same-origin motion, timestamp acceptance and per-frame residual/inlier coverage before calling pose estimates consistent. These gates are diagnostic heuristics, not ground truth or measured scale.

Replay actual completed job artifacts rather than pretending each frame created a new cloud. Bind metrics, points, cameras and downloads to the same completed job. Match source-picture ordinal to the generated 30 fps HLS media clock, while keeping recorded capture elapsed time separate. Historical missing observations are not replaced by nearest-frame claims. Thumbnail planes illustrate camera provenance; they are not geometric surfaces.

## 2026-10-18 — continuous H.264 from Android to PC

**Requirement:** continuously stream actual camera video to the PC for reconstruction. Periodically capturing/uploading still images does not satisfy this requirement.

**Decision:** use Camera2 with preview and MediaCodec encoder surfaces in one repeating recording session. Request H.264 baseline, 1280×720, 30 fps, 2.5 Mbit/s, one-second keyframes, no audio. Send all encoder output, including codec configuration, immediately through one chunked `POST /api/scans/{id}/video` with `Content-Type: video/h264`. Retain existing certificate pinning, bearer authentication and scan ownership. Disable redirects on this transport.

This avoids a new unauthenticated media port, third-party signaling service, and a second trust mechanism. The Android native view owns camera/encoder lifetime; JavaScript only controls the session. A native rebuild is required. Actual device cadence and camera compatibility must be measured rather than inferred from requested encoder settings.

**PC processing:** incrementally parse/decode Annex-B H.264 with PyAV. Preserve the complete received byte stream in a unique file per connection, including partial files after interruption, plus a receipt. Derive live preview and reconstruction inputs after decoding. Select every 60th decoded frame under the 30 fps encoder contract; the existing immutable snapshot scheduler queues at eight views and coalesces subsequent GPU batches. This is model input selection on the PC, not timed image transport from the phone.

**Lifetime and limits:** Pause/background closes the session. Resume opens a fresh independently decodable stream within the same scan. Finish drains the encoder and awaits the PC receipt before freezing. Reject Finish while a receiver is active. Cap one receiver per scan, four process-wide, two GiB / one hour per connection, and 3,600 derived reconstruction views per scan. Preserve earlier source sessions and artifacts.

**Tradeoffs:** H.264 uses inter-frame video compression, a native codec and CPU decoding on the PC. The current transport has no offline video queue or automatic gap recovery. A failed connection is visible; Pause/Resume starts another session. The PC dashboard currently displays decoded JPEG previews, while the preserved/source transport is continuous H.264. VGGT output remains batch-based, unscaled, independently estimated point clouds. Browser-native video playback, automatic reconnect, source-video playback UI, iOS video, long-duration thermal testing and registered SLAM fusion remain separate work.

**Evidence:** automated tests transmit a real 480-frame inter-frame H.264 stream split across arbitrary byte boundaries, verify byte-for-byte source preservation, decode 480 frames, derive eight views and queue reconstruction. The updated development APK built and installed on the connected Samsung phone. The user reported increasing sent KiB; PC runtime inspection confirmed a growing H.264 source and completed/active GPU jobs. Its finalized receipt recorded 48,638,950 bytes and 3,919 decoded frames. The scan included five pre-update JPEGs alongside 66 video-derived views; clean video-only room quality is not established. One finalized video session was observed; a separate resume-session file was not confirmed. See [GATES.md](GATES.md). The original mistake is recorded in [MISTAKES.md](MISTAKES.md).

API references: [Android CameraDevice](https://developer.android.com/reference/android/hardware/camera2/CameraDevice), [MediaCodec](https://developer.android.com/reference/android/media/MediaCodec), [Expo native views](https://docs.expo.dev/modules/native-view-tutorial/), [PyAV incremental H.264 parsing](https://pyav.org/docs/stable/cookbook/basics.html).


## 2026-10-18 ? use phone visual-inertial tracking

The user explicitly requested phone rotation, motion and accelerometer-based camera positioning. Use ARCore 1.57.0 SHARED_CAMERA (version checked in Google's Maven metadata), sharing the camera with the existing H.264 encoder. Do not integrate accelerometer values twice and call the drift a position. ARCore supplies fused physical camera poses; preserve raw IMU measurements for diagnostics.

Render preview through ARCore's display-aware UV transform. The previous implementation sent a landscape buffer directly into a portrait TextureView without a transform, which stretched the view. The observed saved scan also combined earlier upright JPEGs with sideways video views and many near-static repeats; successful inference did not establish room reconstruction quality.

Use encoder timestamps plus ordered access-unit indices and a native session ID to associate poses with PC-decoded views. Save metadata before publishing each selected access unit; preserve an immutable per-job metadata snapshot. Fit external pose alignment only with synchronized, consistent, sufficiently moving cameras from one ARCore origin. Otherwise report unavailable/insufficient/inconsistent tracking and leave VGGT geometry unaligned. This is pose-constrained global alignment after VGGT, not neural sensor fusion or dense visual-inertial mapping. Existing artifacts remain unchanged; historical video has no recoverable IMU track.

References: [Google shared-camera sample](https://github.com/google-ar/arcore-android-sdk/tree/master/samples/shared_camera_java), [physical camera pose](https://developers.google.com/ar/reference/java/com/google/ar/core/Camera), [Android preview transforms](https://developer.android.com/media/camera/camera2/camera-preview).


Device validation required two lifecycle/timing corrections: acquire ARCore internal camera surfaces only after the wrapped device-open callback, and normalize encoder CLOCK_MONOTONIC against ARCore/sensor elapsed realtime on devices with suspend-time offsets. Retain raw and normalized timestamps and continue rejecting invalid matches. ARCore TRACKING alone does not establish synchronized use in reconstruction.


## 2026-10-18 ? no fixed point cap; accumulate tracked batches

The user rejected fixed point limits and separate replacement clouds. Default model export and desktop binary/display caps are removed. Stream jobs override old saved caps, new hardware profiles use zero (uncapped), and all valid filtered points remain available. Model input resolution still determines how many depth samples a batch can predict.

Accumulate accepted ARCore-aligned results for the same origin using spatial deduplication at 0.01 ARCore units, with no cap on occupied cells. Keep snapshot geometry and provenance; combine camera indices consistently. Do not merge unrelated origins or rejected alignments. This produces an accumulated map at batch latency. It does not turn VGGT into video-rate dense SLAM. A separate truly incremental depth/mapping engine remains necessary for that stronger requirement.

The measured timestamp mismatch was not resolved by the generic suspend-offset heuristic. The corrected contract uses ARCore Frame.getAndroidCameraTimestamp() to match encoder PTS, and retains the independent ARCore internal timestamp separately. A regression test covers different internal/camera clocks. See [Frame reference](https://developers.google.com/ar/reference/java/com/google/ar/core/Frame).


Use explicit three-point floor selection and an unscaled grid while automatic floor-plane capture is absent. Do not infer floor height from gravity alone or label an arbitrary reference grid as detected ground. Fix the viewer up-axis for accepted ARCore worlds; preserve source geometry and camera images.


2026-10-18 superseding decision: retain and process every decoded video picture in ordered eight-view windows advancing four, preserving backlog instead of globally sampling. Per-picture tracking is bundled six at a time before video bytes. Provide durable HLS playback with rewind, mobile ARCore floor selection, explicit portrait rotation, actual outgoing fps, and frame-bound camera trajectories. These features do not establish 30 Hz VGGT inference or recover historical missing poses.


Timestamp correction prepared after inspecting saved physical sessions: Android video encoder DEFAULT output can use uptime while REALTIME camera metadata uses elapsedRealtime. Native matching now considers the measured elapsedRealtimeNanos-to-uptimeMillis conversion only for REALTIME sensors, retaining raw encoder PTS, conversion offset and clock-source labels. Choose direct camera-clock matching when it is closer, and retain the server 100 ms rejection gate. This supersedes the failed assumption that getAndroidCameraTimestamp alone shares the encoder clock. The updated clock APK was built after phone disconnection and is NOT installed/physically verified yet. See https://developer.android.com/reference/android/hardware/camera2/params/OutputConfiguration#TIMESTAMP_BASE_DEFAULT and https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_INFO_TIMESTAMP_SOURCE .


Viewpoint evidence is explicit: orange frusta are VGGT image-estimated cameras bound to the displayed job; blue camera centers/path and the selected green frustum require timestamp-valid ARCore samples. Unregistered sensor paths are shown separately from the cloud. Earlier captures without usable timing cannot be presented as sensor-positioned cameras.
