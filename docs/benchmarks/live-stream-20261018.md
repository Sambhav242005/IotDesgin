> Historical sampled-JPEG experiment. This did not validate continuous video transport. The replacement H.264 implementation and physical-phone evidence are recorded in [GATES.md](../../GATES.md) and [DECISIONS.md](../../DECISIONS.md).

# Progressive live-stream reconstruction validation ? 2026-10-18

This validates successive GPU job snapshots and publication using replayed Pexels JPEG fixtures, not a live phone camera or room-quality acceptance. The fixture has the limitations in [the original clip report](pexels-7578017.md).

## Reproduce

From the repository root, with dependencies, official VGGT and its checkpoint already installed, choose a fresh output folder:

```powershell
.\.venv\Scripts\python.exe scripts/validate-live-stream.py --images data/benchmark/pexels-7578017-frames --output data/benchmark/live-stream-20261018-repeat --repo vendor/vggt
```

The script takes the installation's GPU lock, creates isolated runtime data, pairs a test client without printing its credential, uploads eight frames, claims the job, uploads four more while that job is active, and runs the two actual VGGT snapshots. It verifies frozen input counts, successive completed-job publication, browser payload delivery, and clean final completion.

| Snapshot | Input | VGGT metric | Worker-to-payload wall time | Peak allocated CUDA | Peak process RAM | Exported points | Browser payload |
|---|---|---|---|---|---|---|---|
| 1 | 8 ? 336 ? 196 | 14.36 s | 19.52 s | 5,104.72 MiB | 9,311.89 MiB | 263,424 | 2,400,000 bytes |
| 2 | 12 ? 336 ? 196 | 11.77 s | 12.48 s | 5,147.98 MiB | 10,728.78 MiB | 395,136 | 2,400,000 bytes |

Hardware: NVIDIA GeForce RTX 4070 Ti, PyTorch 2.11.0+cu128. FP32 model storage, CUDA mixed-precision inference, export cap 500,000. Progressive jobs omit segmentation. The browser payload samples up to 100,000 points; full PLY outputs are retained. Inference metrics do not include initial camera collection time, phone/network latency, or the viewer's two-second polling interval.

Results live under ignored `data/benchmark/live-stream-20261018/results.json`. The associated isolated scan is `74c170f8-ac54-4652-a327-23c2963b46f4`. Its viewer was opened on a temporary loopback HTTP test server, the actual cloud was visually inspected and Reset view exercised; the screenshot is `data/benchmark/live-stream-20261018/viewer.png`. A subsequent fixture republication changed the visible displayed-snapshot ID from `2f2cf6b3` to `039a7f6a` without page navigation, confirming automatic geometry replacement. This browser-specific check reused the prior real-GPU geometry, without fresh inference; details are in ignored `browser-refresh.json`. It does not establish physical-phone streaming. The temporary test server is separate from the production pinned-HTTPS service.

Each snapshot independently estimates its unscaled coordinates; no cumulative registration/fusion is claimed. Real-phone capture-to-live-3D behavior, real-room geometry quality, interruption recovery on an actual phone, and sustained resource use remain acceptance work.
