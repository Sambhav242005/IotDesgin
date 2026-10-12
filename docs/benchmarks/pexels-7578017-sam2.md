# SAM 2 segmentation validation

Validated locally on 2026-10-12 using Meta's official SAM 2 source, revision `2b90b9f5ceec907a1c18123530e92e794ad901a4`, and `facebook/sam2.1-hiera-small`.

## Input and configuration

- 12 evenly selected JPEG frames from the existing Pexels clip 7578017 extraction.
- Original frames: 1280 x 720; segmentation input: 1024 x 576.
- Windows-native RTX 4070 Ti, PyTorch 2.11.0+cu128, TorchVision 0.26.0+cu128.
- Automatic masks: 16 x 16 prompt grid, 16 prompts per batch, no extra crop layers, at most 64 retained regions per image.
- Regions smaller than 0.1% of image area are omitted. Overlapping retained masks are preserved independently.
- Optional custom CUDA extension and associated hole/sprinkle postprocessing disabled.

## Measured result

| Metric | Result |
|---|---:|
| Frames processed | 12 |
| Retained region masks across frames | 128 |
| Segmentation duration, checkpoint already cached | 6.746 seconds |
| Peak allocated CUDA memory | 736.02 MiB |

Duration includes model loading and mask/preview export. Memory is PyTorch peak allocated memory, not total GPU memory reported by the driver. An earlier three-frame first-download test produced 27 regions in 20.33 seconds.

Outputs are in local ignored storage at `data/benchmark/pexels-7578017-sam2/`: `objects.json`, `objects.npz`, `objects-preview.jpg`, and `objects-previews.zip`. The archive contains all 12 mask/contour overlays without box annotations. `people-preview.jpg` is the `frame_0007` overlay (eighth selected frame) for visual inspection. Overlays were regenerated from the saved masks after removing box rendering; inference metrics refer to the original measured run.

The overlays distinguish some door, wall, railing, and clothing regions. They also show incomplete object coverage and object-part masks. The 128 masks are not 128 unique physical objects. IDs reset per frame. This experiment does not establish persistent identity, semantic names, moving-object removal, or reflection recognition. Existing VGGT point-cloud geometry has not been altered by these masks.

## Reproduce

Stop the GPU worker, then run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m iotdesign.cli segment --images data/benchmark/pexels-7578017-frames --output data/benchmark/pexels-7578017-sam2 --count 12
```

Restart the worker with `--segment-objects` to attach segmentation to future reconstruction jobs. Real-device capture and real-room reconstruction gates remain open.

## References

- [Official SAM 2 source and installation](https://github.com/facebookresearch/sam2)
- [Automatic mask generator](https://github.com/facebookresearch/sam2/blob/main/sam2/automatic_mask_generator.py)
