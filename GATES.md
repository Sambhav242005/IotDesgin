# Gates: Phone to PC room reconstruction

OWNS: server/**, mobile/**, tests/**, scripts/**, docs/**, pyproject.toml, README.md

Scope: native capture, discovery and remembered pairing; durable API and GPU worker; saved scene viewer.

- [x] G1: API tests verify authentication, idempotency, validation, freezing and job recovery
  CHECK: .venv\Scripts\python.exe -m pytest -q
  EXPECT: passed
  EVIDENCE: 6 passed; `ruff check server tests` passed
- [x] G2: Mobile types and connection lifecycle tests pass
  CHECK: npm.cmd test
  EXPECT: mobile checks passed
  CWD: mobile
  EVIDENCE: 4 tests passed; `npm.cmd run typecheck` passed; Android bundle exported
- [x] G3: Android development APK compiles with discovery and pinned transport
  EVIDENCE: Gradle `app:assembleDebug` succeeded; local Kotlin discovery/HTTPS module compiled; APK at `mobile/android/app/build/outputs/apk/debug/app-debug.apk`
- [ ] G4: Real phone discovery, pairing, camera capture and reconnect work on Wi-Fi
  EVIDENCE: pending
- [ ] G5: Real room reconstruction and 8/12/20-frame GPU benchmark complete
  EVIDENCE: The 8/12/20 benchmark ran on Pexels clip 7578017, but its camera is nearly fixed and it does not meet the real-room capture gate; see `docs/benchmarks/pexels-7578017.md`.
- [x] G6: Saved point cloud and cameras load in the desktop viewer
  EVIDENCE: The 20-frame saved scene loaded in the local Viser viewer with 500,000 points and 20 camera poses. It revealed poor geometry for this near-static clip.
- [ ] G7: iPhone native module compiles and discovery works on a device
  EVIDENCE: pending

- [x] G8: Official SAM 2 image segmentation runs on the local GPU and exports reloadable masks
  EVIDENCE: 12 Pexels frames produced 128 region masks in 6.746 seconds at 736.02 MiB peak allocated CUDA memory; see `docs/benchmarks/pexels-7578017-sam2.md`. Subsequent semantic run produced 47 named proposals. Mask IDs remain per frame; motion/material inference remains pending. Explicit review exclusions and restoration are implemented; YOLO missed mirrors in three Mirror3D samples.

- [x] G9: Windows profile selection, existing-environment setup and hidden launcher work
  EVIDENCE: 22 tests pass, including competing GPU lock acquisition; Ruff passes. PlanOnly and Low SetupOnly ran on this Windows host. Hidden server/Low worker started and stopped with a data path containing spaces. Existing installation/services were reused. Fresh-machine bootstrap and physical GTX 1050 Ti remain unvalidated.
- [x] G10: Reduced-memory VGGT and SAM 2 Tiny execute within the configured allocation budget
  EVIDENCE: On RTX 4070 Ti with 3.2 GiB allocator limit, VGGT 8x168x112 completed in 7.26s at 2888.55 MiB allocated VRAM / 9824.73 MiB process RAM. Sequential YOLO/SAM 2 Tiny generated 13 regions at 525.21 MiB VRAM. See quantization report. This is not a physical 1050 Ti benchmark.

- [x] G11: Grounding DINO mirror proposals feed SAM 2 with proposal provenance
  EVIDENCE: 25 tests pass. Real Windows CPU DINO inference and SAM 2 Tiny/Small ran on three Mirror3D images, proposing mirrors on all three. Tiny mask IoUs 0.3500/0.0012/0.2112 remain poor, and Small did not resolve the boundary failures; reliable mirror handling is an open gate. See grounded-mirror report.

- [x] G12: Bounded mirror point prompts and mask refinement run with fallback and provenance
  EVIDENCE: 29 tests pass. Three-sample mean IoU improved Tiny 0.1875→0.3915 and Small 0.1871→0.3641. Sample 45 regressed; no broad accuracy guarantee is established. CUDA peaks remained 525.21/551.79 MiB. See grounded-mirror report for individual results and heuristic limitations.
