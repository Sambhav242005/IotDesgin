# Windows low-memory inference and quantization

Checked 2026-10-12. Target: native Windows, GTX 1050 Ti (Pascal, compute 6.1), 4 GB VRAM, 16 GB RAM. No VLM is included in the pipeline or installer.

## Quantized releases inspected

| Project | Published approach | Decision for the installer |
|---|---|---|
| [QuantVGGT](https://github.com/wlfeng0509/QuantVGGT) | W4A4 calibration parameters plus original VGGT checkpoint | Research candidate; not enabled as a packed 4-bit Windows backend |
| [QVGGT](https://github.com/DDsacu/QVGGT) | Quantization-aware VGGT with an AWQ inference extension | Requires a separate native Windows/Pascal build and validation |
| [FGQ](https://github.com/ypzhng/FGQ) | Fisher-guided 4-bit calibration with FlatQuant fake quantization | Accuracy research; fake quantization alone does not provide packed-weight VRAM savings |
| [HunyuanWorld-Mirror](https://huggingface.co/tencent/HunyuanWorld-Mirror) | Official model card lists approximately 1B parameters and F32 weights | No ready-to-use official 4-bit Windows checkpoint verified; not integrated |

The important detail in [QuantVGGT's runtime source](https://github.com/wlfeng0509/QuantVGGT/blob/main/evaluation/quarot/quarot_linear.py) is that `reparameterize()` assigns quantized/dequantized weights back into `nn.Linear` using the original floating dtype, and evaluation calls that floating-point linear layer. This is evidence that the inspected path does not automatically store and multiply packed 4-bit weights. W4A4 numerical evaluation is not sufficient evidence for a fourfold reduction in installed VRAM.

[QVGGT's extension setup](https://github.com/DDsacu/QVGGT/blob/main/qvggt/runtime/awq_inference_engine/setup.py) uses custom CUDA compilation and GCC/OpenMP flags. Its published build is not a drop-in wheel verified on Windows and compute 6.1. FGQ explicitly describes its FlatQuant dependency as fake quantization.

[bitsandbytes installation documentation](https://huggingface.co/docs/bitsandbytes/main/en/installation) distinguishes Pascal-compatible NF4/FP4 from newer hardware requirements for other kernels. Support in that library does not establish compatibility of our custom VGGT network, its attention operations, or its geometric accuracy. Do not apply a Transformers `load_in_4bit` argument to this native VGGT implementation and assume it works. GGUF language-model loaders are not a VGGT backend.

## Implemented alternative

The Low profile stores the VGGT aggregator in FP16, retains camera/depth heads in FP32, runs FP16 autocast, selects eight evenly distributed images, and resizes input width to 168 pixels. It runs recognition and SAM 2 Tiny sequentially after releasing VGGT. FP16 is reduced floating-point precision, not INT4 quantization. SAM 2 Tiny and YOLO Nano are smaller model architectures, not quantized checkpoints.

The CUDA allocator budget is capped at 3.2 GiB and reduced further if free VRAM is lower. This budget applies to PyTorch allocations; driver/context and other applications consume additional memory. The 150,000-point export cap limits viewer output, not neural inference memory. Original photographs remain preserved.

The installer probes a real FP16 CUDA matrix multiplication. Fresh Pascal installations select PyTorch 2.6.0/torchvision 0.21.0 CUDA 12.4 wheels from the [official previous-version instructions](https://docs.pytorch.org/get-started/previous-versions/). Existing working CUDA installations are retained. Availability alone is insufficient: a wheel can detect a GPU while lacking compatible kernels. BF16/FP8 are not selected for Pascal.

At least 11 GiB of available system RAM and 3.1 GiB free VRAM are required to enable local inference. Close applications before launch on a 16 GB laptop. If prerequisites fail, Auto selects ServerOnly, preserves capture/upload storage and skips GPU model downloads. Queued reconstruction requires a worker; capture-only mode is not a CPU reconstruction implementation.

## Measured evidence

On the RTX 4070 Ti, imposing a 3.2 GiB PyTorch allocation budget and running eight official VGGT kitchen example photographs completed:

| Metric | Result |
|---|---|
| Input | 8 frames, 168 x 112 pixels |
| Precision | FP16 aggregator, FP32 geometry heads |
| Peak allocated VRAM | 2888.55 MiB (2.82 GiB) |
| Peak sampled process RAM | 9824.73 MiB (9.59 GiB) |
| Duration | 7.26 seconds |
| Exported points | 75,264 |

Local raw results: `data/benchmark/windows-low-profile/benchmark.json`. This verifies the memory-reduced path on the available GPU. It does not measure GTX 1050 Ti execution time, prove its kernel compatibility, or satisfy real-phone/real-room quality acceptance. The point cloud remains unscaled. A physical 1050 Ti run is required before calling that laptop configuration validated.

To enable INT4 later, require actual packed weights, native Windows compute-6.1 kernels, cold/warm peak memory measurements including context, saved artifact compatibility, and geometry comparisons against the FP16 baseline. Keep experimental dependencies in a separate environment.
