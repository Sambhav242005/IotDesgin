# Moving objects and combined reflection/motion pipeline

Research date: 2026-10-12. This is a proposed extension, not an implementation or a new acceptance result. Current behavior was checked against `SPEC.md`, `DESIGN.md`, `server/iotdesign/segmentation.py`, and `server/iotdesign/reconstruction.py`.

## Findings from primary sources

**SAM 2 gives image/video masks and temporal association.** Its video API accepts prompts and propagates masklets, supporting multiple tracked objects. Mask prompts and point prompts make a box-free interaction possible. The official predictor exposes CPU offloading for video frames and inference state, trading GPU memory against performance. Neither an object ID nor a moving image boundary proves physical motion. [Official SAM 2 repository](https://github.com/facebookresearch/sam2), [video predictor source](https://github.com/facebookresearch/sam2/blob/main/sam2/sam2_video_predictor.py), [SAM 2 paper](https://arxiv.org/abs/2408.00714).

**A segmentation tracker does not name an object.** SAM 2 is promptable visual segmentation; semantic names require user labels or a separate semantic model. Likewise it does not measure RF behavior, identify mounting suitability, or establish metric dimensions. These are distinct project functions, not properties implied by a good mask. [SAM 2 paper](https://arxiv.org/abs/2408.00714).

**Combining semantic and geometric evidence is established practice.** DynaSLAM combines learned segmentation with multi-view checks to detect dynamics and construct a static scene. Its paper distinguishes potentially dynamic semantic categories from geometrically moving regions; a parked object may become moving, while an unusual moving item may be outside predefined categories. This supports a layered strategy but does not demonstrate that substituting SAM 2 into VGGT automatically achieves DynaSLAM's results. [DynaSLAM paper](https://arxiv.org/html/1806.05620v2), [official code](https://github.com/BertaBescos/DynaSLAM).

**Correspondence is another separate output.** CoTracker3 follows queried image points through time, including visibility/occlusion estimates. Such trajectories can feed geometric residual tests; they do not by themselves distinguish camera motion from object motion or provide object names. [Official CoTracker repository](https://github.com/facebookresearch/co-tracker), [CoTracker3 paper](https://arxiv.org/abs/2410.11831).

**Dynamic reconstruction has different requirements.** MonST3R outputs time-varying point clouds and per-frame cameras. Its official README reports approximately 33 GB VRAM for a 65-frame 16:9 example, reduced to approximately 23 GB by non-batchified optimization. It also documents a window-wise option and a feed-forward mode with reduced quality and a small-camera-motion restriction. Those are upstream examples, not measurements on this project's RTX 4070 Ti. Neither optimized 65-frame figure fits 12 GB. [Official MonST3R README](https://github.com/Junyi42/monst3r), [paper](https://arxiv.org/abs/2410.03825).

MegaSaM offers another research path for cameras and depth from dynamic monocular video. Evaluate it independently if VGGT camera estimates remain poor under motion; it is not an immediate replacement justified by the existing benchmark. [Authors' MegaSaM project](https://mega-sam.github.io/), [CVPR paper](https://openaccess.thecvf.com/content/CVPR2025/papers/Li_MegaSaM_Accurate_Fast_and_Robust_Structure_and_Motion_from_Casual_CVPR_2025_paper.pdf).

## Current project boundary

The current image segmentation path stores overlapping packed masks with frame-local IDs. It does not run video propagation, semantic labeling, or dynamic-point removal. VGGT's export filters confidence and invalid depth; segmentation currently runs afterward. Adding a mask artifact does not improve the exported cloud automatically. Current VGGT inference disables its tracking and point heads, so a proposed correspondence stage must not assume those outputs already exist.

Captures are approximately two seconds apart: 0.5 frames per second. Video propagation can be attempted on these ordered images, but wide appearance changes, entering/exiting objects, long occlusions, and missing intermediate motion make association uncertain. **This is a project risk assessment, not a published minimum SAM 2 frame-rate requirement.** Preserve the sparse-photo path and test a separate opt-in, denser clip path with actual timestamps; do not silently change the capture contract.

## Recommended pipeline: design proposal

1. Preserve originals and calibration/resize metadata. Extract reconstruction keyframes separately from tracking frames. Every mask must map through the exact crop, padding, resize, and orientation transforms into VGGT pixels; a same-sized mask is insufficient if transforms differ.
2. Obtain semantic candidates from a labeler or user correction. Use SAM 2 mask or click prompts to obtain object-shaped regions. Propagate tracks only when sequential evidence supports association; allow re-prompting. Show contours and filled masks rather than boxes. An internal detector's box can be a proposal mechanism without becoming the stored segmentation or viewer interface.
3. Estimate cameras/depth from the original images. Identify a background subset outside confirmed people, mirror apertures, and uncertain material regions. A foreground-dominated frame may need rejection or a request to recapture.
4. Compare image correspondences with the positions predicted by static geometry and camera motion. A static pixel's depth is unprojected into world coordinates and projected into the next view. Persistent disagreement on visible pixels supplies motion evidence. Use robust background estimation and visibility/depth checks; raw optical-flow magnitude also includes camera movement and cannot serve as the motion label.
5. Aggregate evidence over each tracked mask. Keep `motion_state` as `static`, `dynamic`, or `unknown`, independently of semantic class and `material_state`. Occlusion, poor depth, reflection, mask drift, and insufficient baseline must permit `unknown`; missing evidence is not evidence of static geometry.
6. Exclude confirmed moving masks and confirmed reflective/transparent regions from static-cloud export. Preserve rejected observations separately for review. Pixel filtering is a first stage: it does not undo camera/depth errors introduced during VGGT inference. Evaluate cleaner frame selection or a geometry re-estimation stage if errors persist. Do not black out regions and assume VGGT accepts that as an attention mask.
7. Associate surviving masks with 3D points using pixel provenance before export sampling. Fuse only supported static observations. Overlapping part/object masks require explicit ownership rules or multi-label records rather than double-counting physical objects.

These are application recommendations informed by the sources above; none has been validated in this repository yet.

## Reflection plus motion cases

| Observed case | Proposed treatment |
|---|---|
| Moving person seen directly | Keep its mask/track for review; exclude confirmed dynamic pixels from static geometry |
| Moving person visible in a fixed mirror | Track the mirror aperture separately; exclude that aperture regardless of apparent person movement; never create a second physical person behind the wall |
| Camera/operator reflected in mirror | Same mirror exclusion; image motion is not evidence that the mirror moves |
| Mirror carried by someone | Mark the host object dynamic and reflective; preserve both reasons, not a single conflicting label |
| Glossy furniture highlights changing with camera view | Flag material/depth uncertainty; do not label the whole physical object moving solely from highlights |
| Motion behind glass | Flag the transparent region and uncertain geometry; do not treat glass depth as the depth of the visible object behind it |
| Reflection classifier uncertain | Preserve `unknown`, offer mask correction; avoid silently approving a mounting surface |

This table is a proposed handling policy, not a claim that SAM 2 detects these material distinctions. Mirrors produce reflected imagery that need not correspond to a physical surface at inferred depth. Dedicated reflective-surface recognition, geometric checks, and user correction remain necessary.

## Practical 12 GB GPU strategy

Keep one GPU worker and run modules sequentially. Prototype with SAM 2.1 Small/Tiny, bounded tracks, short windows, mixed precision, and CPU-offloaded state where available. Save compact masks between stages; release model/state allocations before the next stage. Benchmark peak VRAM, RAM, time, resolution, frame count, and object count on the actual PC. SAM 2's published FPS table is measured on an A100 and must not be presented as this PC's speed. [Official SAM 2 model table](https://github.com/facebookresearch/sam2#model-description).

Static-room cleanup should be the next milestone. A dynamic 4D viewer requires per-time geometry and additional acceptance criteria; a moving orbit camera around a static PLY is not object-motion reconstruction. MonST3R or MegaSaM can be evaluated separately after installation/license review, with no 12 GB performance promise.

## Acceptance experiments to implement later

Use manually annotated masks on physical room clips as reference; include the existing stock clip only as limited regression evidence. Record mask IoU and boundary quality, track identity switches, motion precision/recall and unknown rates, retained static-background coverage, invalid reflected geometry, camera/depth consistency, and resource usage. Freeze proposed numeric acceptance thresholds before evaluation rather than selecting them after seeing outputs.

- Camera walks through an empty room: stationary furniture must remain static despite image motion.
- Person walks across the same room: compare baseline and masked geometry; measure ghost removal and background loss.
- Person stands still, then moves; an unfamiliar object is moved: separate category priors from observed motion.
- Two similar people cross and occlude each other: record identity swaps and corrections rather than claiming perfect recovery.
- Fixed mirror reflects person and operator; shiny tabletop changes highlights; glass shows movement behind it: keep material and motion explanations separate.
- A moving reflective object: retain both attributes and exclude it from placement geometry.
- Test full-rate clips and deliberate two-second subsampling of the same recording: quantify degradation instead of inventing an fps guarantee.
- Apply masks through portrait/landscape resize, crop, and padding fixtures: ensure point labels and exclusions match original pixels.
- Restart worker during processing and force resource failure: retain originals, tracks/masks, ownership checks, and recoverable jobs.

For IoT placement, segmentation can identify candidate devices/surfaces and separate obstacles. Placement still needs measured scale, supported surface geometry, device-specific height/power/access constraints, and a separate RF model. An extra mask improves interpretation only when correct and correctly associated; greater data volume alone is not guaranteed greater understanding.
