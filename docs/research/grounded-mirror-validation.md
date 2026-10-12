# Grounding DINO mirror proposals and SAM 2 review

Grounding DINO Tiny is now part of `--semantic-objects`: YOLO category proposals → targeted Grounding DINO `mirror.` proposals → SAM 2 masks → existing desktop review. It runs on local CPU, with custom CUDA kernels disabled, after YOLO releases its GPU model and before SAM 2 loads. Images are not sent to a remote inference service. The Windows setup script caches the additional model through the semantic dependency and model-download stage.

[Official Transformers documentation](https://huggingface.co/docs/transformers/model_doc/grounding-dino) describes text-grounded boxes and composition with segmentation. Boxes are internal SAM prompts; the review UI displays masks and contours. The implementation uses `IDEA-Research/grounding-dino-tiny`, detection/text thresholds 0.25, processor shortest/longest edges bounded at 512/768, and target sizes in the exact resized SAM-image coordinates. Duplicate mirror boxes are suppressed without discarding other categories. Model, prompt, score and proposal status are preserved in mask metadata.

## Three-sample check, 2026-10-12

### Point and mask refinement update

The semantic mirror path now retains the original box-only mask, adds nine positive grid points inside the detected box and up to four negative points outside it, requests multiple candidates, and performs one logits-based refinement pass. Points outside the image are omitted. All points are heuristics, not user-confirmed mirror pixels; foreground objects inside the box are not automatically labeled negative because they could be reflections. Up to five candidates are ranked using SAM quality plus box coverage minus leakage, with the original quality threshold retained. Coverage is a surface-selection heuristic, not a calibrated confidence or proof of material.

This follows point/box and previous-mask inputs from Meta's [image predictor notebook](https://github.com/facebookresearch/sam2/blob/main/notebooks/image_predictor_example.ipynb). The user-provided [automatic generator notebook](https://github.com/facebookresearch/sam2/blob/main/notebooks/automatic_mask_generator_example.ipynb) demonstrates denser sampling, crops, stability filtering and mask-to-mask refinement. Its larger grids and batches are not copied into the 4 GB profile. The generic automatic-mask path remains unchanged; this update targets semantic mirror proposals.

| Sample | Tiny box-only IoU | Tiny refined IoU | Small box-only IoU | Small refined IoU |
|---|---|---|---|---|
| 45 | 0.3500 | 0.3008 | 0.3464 | 0.3105 |
| 664 | 0.0012 | 0.6619 | 0.0014 | 0.5682 |
| 686 | 0.2112 | 0.2119 | 0.2134 | 0.2135 |

Tiny mean IoU increased from 0.1875 to 0.3915; Small from 0.1871 to 0.3641. One sample regressed, one improved substantially, and one barely changed. No held-out validation or guarantee follows from these three examples. The heuristic was not tuned per-image using ground truth. Tiny/Small runs took 10.00/9.88 seconds, at 525.21/551.79 MiB allocated CUDA memory. Device and room-quality limitations still apply.

Each region now records its selected strategy, point coordinates/labels, heuristic status, ranking score and candidate count under `sam_prompt`. Exclusions remain explicit review actions; material and motion remain unknown. Raw results are `grounded-results-tiny-refined.json` and `grounded-results-small-refined.json` in the local sample directory. Stop the active worker before running the validation script; it now shares the worker lock. Use `--box-only` for an unchanged box-only comparison.

### Earlier box-only baseline

SAM 2 Small was also checked: IoUs were 0.3464, 0.0014 and 0.2134, with 10.05 seconds total and 551.79 MiB CUDA allocation. Switching from Tiny to Small did not resolve the mask-boundary failure. Its raw results are `grounded-results-small.json` and `grounded-semantic-small/` in the same local sample directory.

The three official Mirror3D example images already downloaded locally were reused. Ground truth is the union of all nonzero mirror-instance pixels. Reported IoU compares the union of proposed Mirror masks, resized back to the original image, against that union. This is a small targeted regression set, not a general benchmark.

| Sample | Earlier YOLO + SAM 2 mirror IoU | Grounding DINO + SAM 2 Tiny proposals | Grounded mask IoU |
|---|---|---|---|
| 45 | 0 | 2 | 0.3500 |
| 664 | 0 | 1 | 0.0012 |
| 686 | 0 | 2 | 0.2112 |

The combined three-image semantic pipeline took 9.48 seconds on this host and peaked at 525.21 MiB allocated CUDA memory. The DINO stage runs on CPU; this CUDA measurement does not measure its RAM use or physical GTX 1050 Ti speed. Local outputs are under `data/benchmark/mirror3d-samples/grounded-semantic/` and `grounded-results.json`; reruns with `scripts/validate-mirror-grounding.py` use size-suffixed output folders and reports.

Targeted detection found candidates in all three images where YOLO had found none. Mask accuracy remains poor, particularly sample 664, where segmentation can select reflected contents rather than the physical mirror boundary. Do not equate a mirror box, a high SAM score or a proposed label with a confirmed reflective-surface mask. Reject poor masks during review. No automatic exclusion, completeness guarantee, pose repair, motion detection or measured placement is introduced. The integration is a candidate-generation improvement, not reliable mirror handling.
