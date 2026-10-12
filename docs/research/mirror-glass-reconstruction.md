# Mirrors, glass, and reflective surfaces in IoTDesign

Research date: 2026-10-12. This note proposes experiments; it does not report implemented mirror handling or validated hardware performance. The current application has optional SAM 2 image-region masks, not material recognition, persistent object tracking, or mask-aware reconstruction filtering.

## What the models provide

SAM 2 provides prompted image masks and video mask propagation. Point prompts and corrections can select a mirror region without displaying bounding boxes. Its video state can offload frames and state to CPU. A mask is a selected region, not evidence that the region is a mirror or that its depth is physical. These capabilities were checked through Context7's `/facebookresearch/sam2` documentation and the [official repository](https://github.com/facebookresearch/sam2).

Mirror geometry needs a separate treatment. A mirror can contain a convincing reflected room; ordinary reconstruction can place that reflected content behind the mirror. Mirror-NeRF explicitly models reflected rays to avoid reconstructing the reflection as an independent virtual scene. Its objective is reflection-aware scene rendering, not a lightweight segmentation add-on. [Mirror-NeRF paper](https://arxiv.org/abs/2308.03280)

## Primary research and practical fit

| Work | Verified contribution | Fit for IoTDesign |
|---|---|---|
| Mirror3D / Mirror3DNet, CVPR 2021 | Predicts mirror planes using RGB and surrounding depth, then refines mirror-region depth. Dataset includes 7,011 mirror instance masks and planes. Official implementation supports RGB-estimated-depth and RGBD paths. | Strong conceptual baseline for separating the physical plane from reflected content. Evaluate in an isolated environment before any integration. |
| Mirror-NeRF, 2023 | Reflection-aware ray tracing addresses the virtual-scene interpretation of mirrors. | Later appearance/reconstruction research; do not make it a dependency for first mask filtering. |
| NeuS-HSR, CVPR 2023 | Official implementation targets neural surface reconstruction under strong specular reflections, provides real/synthetic examples and a mirror case. | Separate surface-reconstruction experiment. Glass appearance and underlying geometry require an explicit objective. |
| NeRF-MD, 2025 | Uses residual photometric inconsistency after initial NeRF geometry training to detect mirror regions, fits surface primitives, then jointly optimizes mirror geometry and the radiance field without prior mirror annotation. | Supports using inconsistency as a candidate signal; the full method includes training and is not equivalent to a VGGT confidence threshold. |
| Reflect3r, 3DV 2026 | Uses a mirror reflection as an auxiliary view through a physically valid virtual camera, with DUSt3R-based reconstruction and per-frame dynamic-scene extension. Official repo supplies a mirror detector and example PLY output. | Particularly relevant experimental comparison for mirror + moving-object scenes; different pipeline from current VGGT and requires independent validation. |

Sources: [Mirror3D paper](https://openaccess.thecvf.com/content/CVPR2021/html/Tan_Mirror3D_Depth_Refinement_for_Mirror_Surfaces_CVPR_2021_paper.html), [official Mirror3D code](https://github.com/3dlg-hcvc/mirror3d), [official NeuS-HSR code](https://github.com/JiaxiongQ/NeuS-HSR), [NeRF-MD paper](https://arxiv.org/abs/2501.04074), [official Reflect3r code](https://github.com/jingwu2121/reflect3r).

Mirror3D's repository documents Python 3.7.4 and Detectron2 dependencies; treat environment compatibility as a migration experiment. Its repository is MIT-licensed, but model and underlying dataset terms need their own review. Reflect3r documents an external detector-weight download, DUSt3R dependencies, Docker reproduction, and MIT repository licensing; verify downloaded weight and dependency terms separately. Neither source establishes runtime or peak VRAM on this project's RTX 4070 Ti. [Mirror3D setup](https://github.com/3dlg-hcvc/mirror3d), [Reflect3r setup](https://github.com/jingwu2121/reflect3r)

## Proposed first pipeline

The following is an engineering recommendation inferred from the sources, not a reported capability of any single model.

1. Preserve source RGBs. Generate candidate semantic/material labels separately, then use SAM 2 to refine full-resolution instance masks. Allow positive/negative point corrections and explicit user-confirmed mirror/glass labels. Render colored mask overlays and contours rather than object boxes.
2. Keep independent fields: semantic label, material state (`mirror`, `transparent`, `specular`, `opaque`, `unknown`), motion state, confidence, and annotation provenance. A shiny metal appliance is not automatically a mirror; a glass door is not an empty doorway.
3. Estimate camera/point geometry with VGGT on the original observations. Match source-mask coordinates to model crop/resize coordinates exactly. At export/fusion, exclude confirmed reflective/transparent regions and dynamic regions from physical-room points. Post-export filtering cannot repair camera estimates already corrupted by those pixels; measure camera quality and require rescan when background support is insufficient.
4. Keep uncertain material regions in an explicit uncertainty layer. Do not silently classify every cross-view inconsistency as reflective: motion, occlusion, wrong camera pose, exposure, and poor texture can produce similar symptoms.
5. For a confirmed flat mirror or window, fit a physical surface from reliable frame/border/surrounding geometry, using cross-view reprojection and sufficient baseline. Validate plane support and residuals. Coplanarity with the wall is a hypothesis, not a guarantee: tilted mirrors and recessed windows require independent evidence. If support fails, retain a 2D mask and unknown depth instead of inventing a plane.
6. Project the validated mask boundary onto the accepted plane to obtain a polygonal physical surface. Mark it `inferred_plane` with confidence and provenance. Do not fill it with reflected colors interpreted as real texture or duplicate reflected furniture into room objects.
7. Run semantic prompting, SAM 2, and VGGT sequentially under the existing one-GPU-worker lock. Start with the already integrated SAM 2.1 Small; persist masks on CPU and evaluate video-state offloading when tracking is introduced. Record actual memory and latency before increasing model size or object count.

This pipeline produces segmentation masks, labeled point subsets, and possibly validated planar polygons. It does not automatically create a complete watertight mesh or recover arbitrary transparent-object geometry.

## Mirror and motion together

For room mapping, mask the full confirmed mirror region regardless of whether the reflection contains a stationary or moving person. Separately mask the directly visible person's track. This keeps a reflected person from becoming a second physical occupant or IoT target. Store direct/reflected/unknown observation provenance when retaining object detections. Do not merge tracks solely because their labels match.

Motion residuals inside mirror masks should not drive physical-object velocity estimates. Outside them, motion classification must account for camera movement. SAM 2 mask propagation tracks a prompted image region; it does not by itself establish rigid physical motion, metric position, or identity through a mirror.

An advanced reflection-aware branch could evaluate Reflect3r on direct + reflected views of a dynamic subject, but its per-frame geometry is distinct from static room fusion. Mixing that output into the room map needs alignment, scale, temporal identity, and physical/virtual provenance checks. These are proposed integration gates, not tested capabilities.

## IoT placement implications

Masked objects help identify observed mounting surfaces and obstacles; mirror exclusion prevents recommendations inside a reflected virtual room. Placement still needs a measured scale, valid surface normal, sufficient observed area, device-specific mounting/coverage rules, and unknown-space handling. Segmentation alone cannot establish RF attenuation, material thickness, concealed wiring, safe mounting strength, or the model of an IoT device.

Retain physical mirror/window polygons as surfaces even when their erroneous depth points are excluded. An unknown hole must not be treated as free space. RF behavior would require material confirmation and separate measurements/modeling.

## Evaluation before claiming success

Use independently annotated masks and a measured room layout, with scenes covering: no mirror; flat wall mirror; tilted mirror; glass door; shiny appliance; moving person; person moving in mirror and directly; and camera motion with a stationary person. Include both controlled and real-phone captures.

Compare unfiltered VGGT, manual-mask filtering, automatic-mask filtering, plane replacement, and any reflection-aware experimental method. Measure mask IoU/boundary quality, mirror false negatives, ordinary-region false positives, ghost geometry behind mirrors, physical-plane position/normal error, camera drift, object-ID switches, static room completeness, runtime, peak VRAM, and RAM. Keep the test masks independent of prompts used to produce predictions. Publish failures and uncertain regions rather than equating a denser cloud with greater correctness.

No mirror/glass experiments or real-room acceptance checks were executed for this research note. Only primary-source and current documentation inspection was performed.
