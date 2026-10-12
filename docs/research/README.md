# Object understanding and IoT placement

Research completed on 2026-10-12 by three parallel research agents and consolidated against the current implementation. Recommendations below are pending experiments unless explicitly marked implemented.

## Reports

- [Object identification and IoT placement](sam2-object-identification-placement.md): semantic grounding, small-device detection, mask-to-3D association, scale and placement requirements.
- [Mirrors and glass](mirror-glass-reconstruction.md): physical versus reflected geometry, material masks, plane recovery and reflection-aware alternatives.
- [Motion and the combined pipeline](moving-objects-combined-pipeline.md): tracking, camera-motion compensation, mirror/motion overlap and evaluation.

## Why SAM 2 belongs in this pipeline

SAM 2 supplies a pixel mask for a selected region. That mask allows an object or surface to be reviewed, tracked in image space, and eventually associated with reliable scene points. A box covers surrounding pixels; a mask follows a more precise visible boundary. The product displays masks and contours. Some recognition models use boxes internally to prompt SAM 2; that does not require a box-based user interface.

SAM 2 alone does not name the device, determine physical motion, classify reflectivity, recover scale, or choose its installation location. Use independent evidence for each task:

| Question | Proposed evidence |
|---|---|
| Which pixels belong to it? | SAM 2 mask, quality score, user correction |
| What object is it? | Grounding DINO or another recognition model, prompt, confidence, confirmation |
| Where is it in the room? | Mask-to-depth mapping, camera pose, multi-view agreement, calibrated scale |
| Is it moving? | Temporal tracks plus camera-compensated geometry and occlusion checks |
| Is it a mirror/glass region? | Material recognition, user confirmation, consistency cues |
| Where should a new IoT device go? | Validated support surface, device-specific mounting/coverage rules, power/network constraints |

## Recommended implementation sequence

1. **Semantic mask review.** Benchmark local Grounding DINO Tiny followed by the installed SAM 2.1 Small. Save proposed labels and their provenance separately from mask scores. Display masks/contours, permit point corrections and unknown labels. Evaluate close-ups and cropped prompts for small devices; current automatic filtering can discard them.
2. **Geometry association.** Preserve source-pixel transformations through resize/crop/padding and point provenance before the 500,000-point cap. Project reliable masked depth into scene coordinates and fuse consistent cross-view observations. Keep incomplete or conflicting associations explicit. Offer colored object-point layers; a segmented cloud is not a closed surface mesh.
3. **Motion and material layers.** Evaluate denser local clips for tracking instead of assuming every-two-second stills provide reliable identity. Compensate camera motion before assigning object motion. Exclude confirmed full mirror/glass regions and dynamic points from the static map; this cleanup cannot repair camera poses already estimated incorrectly. Add camera-quality/rescan gates and evaluate mask-aware estimation separately.
4. **Physical mirror surfaces.** Accept an inferred polygon only if border/surrounding measurements support its plane and orientation. A tilted mirror cannot simply inherit its wall's plane. Unknown depth remains unknown; a gap in the cloud is not free space. Keep directly visible objects distinct from reflected observations.
5. **Measured IoT placement.** Establish metric scale, room vertical direction, support surfaces, object locations and uncertainty. Consult exact-device specifications. Separate locating an installed device from recommending a new location. Visual geometry alone does not establish RF attenuation, concealed wiring, mounting strength, or an exact device model.

Run recognition, segmentation and geometry models sequentially under the existing GPU-worker lock. Record runtime and memory for the complete pipeline; the measured SAM-only memory result does not establish combined resource requirements. SAM 3, ConceptGraphs, Reflect3r, and dynamic-reconstruction systems are separate comparison candidates in the reports, not installed capabilities.

## What is implemented now

- Official SAM 2.1 Small automatic image-region segmentation, standalone and optional after VGGT in the worker.
- Packed overlapping masks, frame metadata, quality scores and colored mask/contour previews without box overlays.
- Twelve Pexels frames tested: 128 region proposals in 6.746 seconds with 736.02 MiB peak allocated CUDA memory. These are per-frame proposals, not 128 distinct objects.
- Authenticated artifact access and ten passing server/export tests.

Object names, persistent tracks, motion/material classification, point filtering, 3D object IDs and placement suggestions remain unimplemented. The [SAM 2 benchmark](../benchmarks/pexels-7578017-sam2.md) records the measured test separately from these proposals.

## Validation before relying on placement

Use real rooms with measured references and independently annotated masks. Include tiny sensors, similar-looking devices, no-device scenes, static people during camera motion, moving people, stationary and tilted mirrors, glass doors, and direct/reflected people together. Measure missed/wrong labels, boundary quality, track switches, ghost geometry, physical-surface error, object-position error, unknown-space handling, GPU memory and runtime. Manufacturer and RF requirements need their own device-specific checks.

More data helps when it adds sharp, overlapping, independent views or confirmed attributes. More repeated frames, uncertain labels, or reflection artifacts can reduce map quality. Keep provenance, uncertainty and user corrections as first-class scene evidence.
