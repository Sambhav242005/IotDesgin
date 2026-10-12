# Semantic masks and IoT placement research

Research date: 2026-10-12. Scope: recommendations, not implemented behavior or measured performance. Read against the current [specification](../../SPEC.md) and [design](../../DESIGN.md). Current integration provides independent SAM 2.1 image masks; semantic naming, persistent tracks and 3D object associations are pending.

## Why add SAM 2

SAM 2 provides prompted image segmentation and video mask propagation. Its contribution is the pixel region occupied by an object, including irregular boundaries; it does not supply the object's category name or its physical position. Automatic region proposals can represent object parts, overlapping regions or background. [Meta SAM 2](https://github.com/facebookresearch/sam2)

For IoTDesign, masks let us attach evidence to specific visible surfaces, select object points instead of entire rectangular image regions, and let the user inspect/correct the selection. More images help only when they add consistent observations, viewpoint overlap and visible detail. Repeated blurry frames, incorrect labels and reflected images add misleading evidence rather than understanding. This is a design inference, not a claimed benchmark result.

## Naming the regions

Recommended first experiment: local Grounding DINO Tiny plus the existing SAM 2.1 Small. Grounding DINO receives category names or referring expressions and returns grounded detections. Hugging Face Transformers exposes a local pretrained model and processor; the labels are text-grounding outputs, not SAM labels. [Grounding DINO documentation](https://huggingface.co/docs/transformers/model_doc/grounding-dino)

The model authors' Grounded SAM 2 repository demonstrates sending Grounding DINO boxes to SAM 2 to produce masks, exporting class names/scores plus masks, and using the masks as video prompts. Boxes may remain internal inference prompts; the product can display only colored masks, boundaries and labels. The reference first-frame tracking demo can miss later arrivals; its continuous-ID variant is described as unstable. Grounding DINO 1.5/1.6 and DINO-X examples use cloud credentials, so they are not the default for this project's local-only processing. [Grounded SAM 2](https://github.com/IDEA-Research/Grounded-SAM-2), [local Hugging Face demo source](https://raw.githubusercontent.com/IDEA-Research/Grounded-SAM-2/main/grounded_sam2_hf_model_demo.py)

Example proposal vocabulary: person, wall, floor, ceiling, door, window, mirror, chair, table, television, router, security camera, thermostat, smoke detector and electrical outlet. This vocabulary is a test plan, not a guarantee that the model recognizes every device. Use generic labels and unknown states where a small device or ambiguous appearance defeats identification. An image cannot establish an exact SKU, network identity or installation specification without additional evidence.

The current [automatic segmentation implementation](../../server/iotdesign/segmentation.py) limits the longest edge to 1024 pixels, uses a 16 x 16 prompt grid, discards regions smaller than 0.1% of the processed image, and keeps at most 64 masks per frame. At 1024 x 576, the area cutoff is approximately 590 pixels. Small wall sensors can be missed by the prompt grid, discarded by the area threshold, or lose the limited region budget. Add prompted/cropped inference and higher-detail close-ups for small IoT devices; preserve the crop transform when lifting masks to scene points. Exact model identification should remain manually confirmed. The updated preview displays masks and contours; bounding boxes remain internal metadata.

Alternative: Florence-2 offers object detection, dense region captions and phrase grounding; its base model has 0.23B parameters. Its labels/captions could propose categories before SAM refinement, but generated descriptions need confirmation and it is not an electrical-device catalog. [Microsoft Florence-2 model card](https://huggingface.co/microsoft/Florence-2-base)

Current alternative worth evaluating separately: official Meta SAM 3 supports text/visual prompts for concept segmentation and tracking, with a detector and tracker sharing an encoder. Its model has 848M parameters, and its checkpoints require an access request. This could simplify semantic mask generation, but memory, checkpoint terms, platform compatibility and room accuracy have not been measured here. Do not describe it as installed or proven on this PC. [Meta SAM 3](https://github.com/facebookresearch/sam3)

## From image masks to 3D object regions

VGGT estimates intrinsics, extrinsics and depth/point maps. Its extrinsics use OpenCV camera-from-world convention. Its geometry helper unprojects depth into world points. [VGGT official usage](https://github.com/facebookresearch/vggt#detailed-usage)

Proposed pipeline:

1. Preserve source images and precise pixel transforms for every inference input. Apply the same resize, crop and padding mapping to masks before indexing VGGT depth. Nearest-neighbor mask resizing preserves categories; merely scaling width and height is insufficient when cropping exists.
2. For each accepted mask pixel with reliable depth, obtain its scene point. Conceptually, `X_camera = depth * inverse(K) * [u,v,1]` and `X_world = inverse(T_camera_from_world) * X_camera`. Use VGGT's helper and test the conventions rather than duplicate assumptions.
3. Keep point-to-frame/pixel provenance before export sampling, so every retained PLY point can receive an object ID. Assigning IDs after flattening/capping without provenance loses the association.
4. Fuse observations only when projected overlap, depth agreement and category evidence agree. Retain conflicting labels, source frame IDs and uncertain associations. A SAM video ID is an observation track, not automatically a permanent physical-object identity.
5. Show selected object points in 3D and the source masks in 2D. Provide hidden/visible object toggles and user corrections. Segmented points are not yet a closed surface mesh.

ConceptGraphs supplies a primary precedent for object-centric 3D maps using region features and geometry. Its code expects posed RGB-D images and offers class-agnostic SAM or tagging/detection-guided SAM variants. Adapting the pattern to VGGT's estimated depths requires an adapter and uncertainty handling; RGB-only inferred geometry should not inherit RGB-D accuracy claims. [ConceptGraphs official code](https://github.com/concept-graphs/concept-graphs), [paper](https://arxiv.org/abs/2309.16650)

## What placement needs beyond segmentation

The following are proposed application requirements rather than outputs of SAM:

- Separate locating an already-visible IoT device from recommending a location for a new device.
- Establish metric scale with a measured reference and establish vertical orientation before reporting mounting heights or distances.
- Recover the support surface and local normal from reliable geometry. A visible object centroid does not determine mounting pose, orientation or clearance.
- Store device-specific field of view, mounting rules, power/network availability and user requirements in an explicit catalog. Link model-specific manufacturer instructions when selecting an actual device.
- Rank candidate support patches with coverage, visibility, obstacle and uncertainty criteria. Exclude mirror/glass regions from trusted mounting geometry until reviewed.
- Keep RF claims separate: visual masks do not reveal wall composition, thickness or measured propagation. RF placement needs material assumptions and later RSSI calibration.
- Present a suggested position with supporting observations and uncertainty; permit user confirmation and correction.

## Practical stages on this PC

These are engineering choices to validate, not invented RTX 4070 Ti benchmarks:

1. Benchmark Grounding DINO Tiny on preserved room images, then run SAM 2.1 Small sequentially. Preserve the existing single GPU worker and release model memory between VGGT/recognition/segmentation stages. Avoid loading all three models together by default.
2. Save semantic labels separately from SAM mask quality scores. Include model/checkpoint revisions, original prompt, detector score, mask score and user override. Scores are not interchangeable calibrated probabilities.
3. Add mask-only review to the desktop viewer. Export masks and contours; keep internal boxes optional diagnostic metadata.
4. Add provenance-aware mask-to-point lifting and multi-view object fusion. Until validated, report positions in unscaled scene coordinates.
5. Add measured support surfaces and a device catalog, then conservative placement suggestions. Validate installed devices and proposed positions separately.

Measure runtime, peak allocated/reserved VRAM, RAM, source/inference dimensions, missed small devices, wrong semantic labels, mask IoU, cross-view ID errors, and position error after scale calibration. Include no-device negative examples and visually similar objects. Do not equate point count, mask count or more model stages with accuracy.

## Evidence and limitations

Context7 was used first to resolve `/idea-research/grounded-sam-2` and query local detection-to-mask usage. Official repositories, model cards and the primary ConceptGraphs paper were then consulted. No models were installed, no jobs were run, and no performance claims were validated in this research task. The near-static Pexels doorway sample cannot validate IoT-device identification or useful placement; these require a real room with known devices and measured reference geometry.
