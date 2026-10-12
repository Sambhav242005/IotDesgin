# Grounding DINO mirror proposals and SAM 2 review

Grounding DINO Tiny is now part of `--semantic-objects`: YOLO category proposals → targeted Grounding DINO `mirror.` proposals → SAM 2 masks → existing desktop review. It runs on local CPU, with custom CUDA kernels disabled, after YOLO releases its GPU model and before SAM 2 loads. Images are not sent to a remote inference service. The Windows setup script caches the additional model through the semantic dependency and model-download stage.

[Official Transformers documentation](https://huggingface.co/docs/transformers/model_doc/grounding-dino) describes text-grounded boxes and composition with segmentation. Boxes are internal SAM prompts; the review UI displays masks and contours. The implementation uses `IDEA-Research/grounding-dino-tiny`, detection/text thresholds 0.25, processor shortest/longest edges bounded at 512/768, and target sizes in the exact resized SAM-image coordinates. Duplicate mirror boxes are suppressed without discarding other categories. Model, prompt, score and proposal status are preserved in mask metadata.

## Three-sample check, 2026-10-12

SAM 2 Small was also checked: IoUs were 0.3464, 0.0014 and 0.2134, with 10.05 seconds total and 551.79 MiB CUDA allocation. Switching from Tiny to Small did not resolve the mask-boundary failure. Its raw results are `grounded-results-small.json` and `grounded-semantic-small/` in the same local sample directory.

The three official Mirror3D example images already downloaded locally were reused. Ground truth is the union of all nonzero mirror-instance pixels. Reported IoU compares the union of proposed Mirror masks, resized back to the original image, against that union. This is a small targeted regression set, not a general benchmark.

| Sample | Earlier YOLO + SAM 2 mirror IoU | Grounding DINO + SAM 2 Tiny proposals | Grounded mask IoU |
|---|---|---|---|
| 45 | 0 | 2 | 0.3500 |
| 664 | 0 | 1 | 0.0012 |
| 686 | 0 | 2 | 0.2112 |

The combined three-image semantic pipeline took 9.48 seconds on this host and peaked at 525.21 MiB allocated CUDA memory. The DINO stage runs on CPU; this CUDA measurement does not measure its RAM use or physical GTX 1050 Ti speed. Local outputs are under `data/benchmark/mirror3d-samples/grounded-semantic/` and `grounded-results.json`; reruns with `scripts/validate-mirror-grounding.py` use size-suffixed output folders and reports.

Targeted detection found candidates in all three images where YOLO had found none. Mask accuracy remains poor, particularly sample 664, where segmentation can select reflected contents rather than the physical mirror boundary. Do not equate a mirror box, a high SAM score or a proposed label with a confirmed reflective-surface mask. Reject poor masks during review. No automatic exclusion, completeness guarantee, pose repair, motion detection or measured placement is introduced. The integration is a candidate-generation improvement, not reliable mirror handling.
