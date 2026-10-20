# Implementation mistakes

## 2026-10-20 — distinguish capture clocks from generated playback time

During showcase development, the first timeline draft used recorded encoder elapsed time as the browser seek coordinate. Review found the existing raw-H.264 FFmpeg remux generates media timestamps at 30 fps, so those clocks can diverge. The final contract uses source-picture ordinal for this media timeline and stores measured capture elapsed time separately. Tests deliberately use a 40 ms capture interval with a 33.33 ms playback interval to prevent conflating the two again. This correction does not repair the historical phone-to-camera clock mismatch.

## 2026-10-18 — continuous video was incorrectly implemented as timed JPEG uploads

The user asked for continuous camera video streamed from the phone to the PC and used for reconstruction. The implementation instead copied preview JPEGs approximately every two seconds, uploaded them through the photo endpoint, and called the result live streaming. Repeated explanations and connection workarounds did not correct that mismatch. Claims that the requested streaming feature was complete were premature.

The correction uses a native Camera2 recording session feeding a MediaCodec H.264 encoder directly. One persistent, authenticated, certificate-pinned HTTPS request carries encoded video to the PC. The PC preserves the received H.264 source, decodes it, and selects views for VGGT. Android stream scans no longer use a JavaScript photo timer, preview bitmap capture, or JPEG upload as their source transport.

Continuous video transport and continuous reconstruction are separate requirements. The transport is continuous video; VGGT still processes selected decoded views in GPU batches. Its successive point clouds are independent, unscaled estimates, not real-time SLAM fusion. Desktop JPEG preview refresh is also separate from the original incoming video.

Validation must prove encoded bytes arrive from a physical camera, are preserved and decoded on the PC, and drive reconstruction while capture remains open. A test using existing JPEG fixtures cannot establish that. Current validation and remaining limits are recorded in [GATES.md](GATES.md). See [DECISIONS.md](DECISIONS.md) for the replacement architecture.

For future changes, preserve the user's transport requirement explicitly in the specification, distinguish model input selection from source transport, and verify the complete requested path before reporting completion.


## 2026-10-18 ? missing preview transform and phone motion tracking

The continuous-video implementation mapped a landscape sensor image directly into a portrait viewfinder, stretching the preview. It also omitted phone sensor/visual-inertial camera poses, using image-only VGGT estimates. The saved scan mixed previous upright JPEGs with raw sideways video views; many sampled video views repeated an almost unchanged scene. Reporting completed jobs and point counts did not demonstrate usable room geometry.

Replace the preview with ARCore display-aware rendering and add synchronized visual-inertial camera poses plus raw IMU metadata. Use verified pose alignment and explicit tracking quality states, preserve historical inputs, and keep room quality as a separate gate. Do not claim the old distorted scan has been repaired merely by installing tracking support; its missing motion measurements cannot be reconstructed retrospectively.


## 2026-10-18 ? fixed point caps and independent batch replacement

The implementation silently limited exports to 500,000 points and the live viewer to 100,000 points, while replacing each preceding cloud. These choices did not match the user's requested growing live reconstruction. Remove the default caps and accumulate accepted synchronized tracked geometry, while explicitly distinguishing batch-latency accumulation from video-rate dense SLAM. A point-count increase alone is not evidence of improved accuracy.

The first tracking matcher incorrectly compared encoder PTS with ARCore's internal frame clock. A generic clock-offset attempt was insufficient. Use the API-provided Android camera timestamp for matching, retain both clock values, and reject unsynchronized data. Never report tracking data as used merely because ARCore reached TRACKING.


## 2026-10-18 - unfinished room blocked starting a different room

The mobile app forced the user to resume/finish a saved scan before starting another room, including scans with too few inputs to finish. Remove that gate, preserve queued uploads with their source scan IDs, expose New room during capture, and list unfinished rooms separately for explicit resume. Scope upload counts to the active room.


The viewer used negative Y-up for every scene, including accepted ARCore world coordinates. A visible cloud and phone orientation do not establish a floor; the new viewer distinguishes unknown floor, gravity orientation, and a user-selected floor. The landscape thumbnail reflects raw encoder dimensions and is not proof that the phone was held horizontally.


The video transport was continuous, but the app still displayed photo-count/checkmark UI and the PC displayed JPEG previews. That obscured what was actually sent and prevented rewind. Keeping one of every 60 decoded frames and selecting globally again also failed the all-frame requirement. Correct these independently: real encoded-video playback, every-frame durable coverage, phone floor selection, rotation metadata, and sensor camera-path evidence. Unit tests and builds must not be described as physical device acceptance.


getAndroidCameraTimestamp is the Android camera metadata clock, but it is not guaranteed to equal the default MediaCodec output clock. Saved physical sessions still showed approximately a 26-hour difference. The next APK considers the documented encoder-uptime conversion for REALTIME sensors and preserves all raw timing evidence. It has not been installed or accepted physically after phone disconnection; do not mark this clock bug resolved.
