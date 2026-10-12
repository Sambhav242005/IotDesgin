# Project structure

## Source and documentation

```text
IotDesign/
|-- AGENTS.md                 Repository development instructions
|-- Project-Structure.md      This source map
|-- DESIGN.md                 Architecture and implementation decisions
|-- SPEC.md                   Requirements and acceptance status
|-- README.md                 Installation and operation
|-- GATES.md                  Recorded validation gates
|-- pyproject.toml            Python dependencies, CLI, and tool configuration
|-- server/iotdesign/
|   |-- cli.py                serve, worker, view, benchmark, doctor, openapi
|   |-- config.py             Data directory, network settings, and limits
|   |-- api.py                Mobile API and loopback desktop endpoints
|   |-- models.py             API request and response models
|   |-- db.py                 SQLite schema and persistence helpers
|   |-- security.py           Certificates, credential hashing, pairing codes
|   |-- discovery.py          Windows-host mDNS advertisement
|   |-- worker.py             Job claiming, heartbeat, recovery, GPU lock
|   |-- reconstruction.py     VGGT inference, filtering, export, metrics
|   |-- segmentation.py       Optional official SAM 2 image-region masks
|   |-- viewer.py             Saved-scene Viser viewer
|   `-- desktop.html          Pairing station and scan/job dashboard
|-- mobile/
|   |-- AGENTS.md             Mobile-specific development instructions
|   |-- App.tsx               Connection, pairing, capture, and upload UI
|   |-- index.ts              Native app entry
|   |-- app.json              Expo configuration and platform permissions
|   |-- package.json          Dependencies and scripts
|   |-- src/protocol.ts       Address, QR, API-version, discovery decisions
|   |-- tests/                Protocol tests
|   `-- modules/iotdesign-network/
|       |-- src/index.ts      Typed native interface
|       |-- android/src/      Kotlin NSD and pinned HTTPS implementation
|       `-- ios/              Swift Bonjour and pinned HTTPS implementation
|-- tests/test_api.py         API and job-recovery tests
|-- tests/test_segmentation.py Mask export and overlap round-trip tests
|-- scripts/setup-sam2.ps1    Install the pinned optional SAM 2 source
`-- docs/
    |-- openapi.json          Exported API schema
    |-- research/             Object names, mirrors/glass, motion, and IoT placement reports
    `-- benchmarks/
        |-- pexels-7578017.md          Benchmark results and limitations
        `-- pexels-7578017-viewer.html Three.js sample showcase
```

## Local and generated files

Windows entry point: `Start-IoTDesign.cmd` invokes `scripts/start-windows.ps1`. `hardware.py` detects resources and selects a profile; `setup_models.py` caches selected checkpoints; `launcher.py` starts/stops verified background services. Bootstrap files are ignored under `tools/`.

Object understanding: `recognition.py` proposes labels, `segmentation.py` creates masks, `object_geometry.py` maps masks to preserved point provenance and applies reversible review edits, and `review.html` displays mask review. Hardware/launcher tests are in `tests/test_hardware.py`; low-memory findings are in `docs/research/windows-low-memory-quantization.md`.

`data/` holds the SQLite database, server certificate material, uploaded images, reconstruction artifacts, and benchmark media/results. Its default database is `data/iotdesign.sqlite3`. A job exports to `data/scans/<scan-id>/artifacts/<job-id>/`.

`vendor/vggt/` is the local official VGGT checkout. Downloaded checkpoint storage is managed by the model-download library. `.venv/` and `mobile/node_modules/` contain dependencies.

`mobile/android/` and `mobile/ios/` are generated native projects. Configure their behavior through Expo configuration and plugins according to `mobile/AGENTS.md`. The local debug APK is generated at `mobile/android/app/build/outputs/apk/debug/app-debug.apk`.

Runtime data, vendored models, dependency directories, and generated native projects are ignored by Git. Native-module build directories are also generated outputs; consult `.gitignore` before adding files.

## Change routing

`server/iotdesign/prompts.py` builds bounded mirror point prompts, compares candidates and refines from low-resolution logits. `tests/test_prompts.py` checks image-edge behavior, ranking, fallback and refinement inputs.

The semantic recognition stage also uses CPU Grounding DINO Tiny for targeted mirror proposals. `scripts/validate-mirror-grounding.py` compares resulting masks against three local Mirror3D example ground-truth masks; `docs/research/grounded-mirror-validation.md` records the limitations.

| Change | Primary files |
|---|---|
| Discovery and connection decisions | `mobile/App.tsx`, `mobile/src/protocol.ts`, native network module, `server/iotdesign/discovery.py` |
| Pairing and transport trust | Native network module, `security.py`, `api.py` |
| Capture and resumable uploads | `mobile/App.tsx`, frame-upload route in `api.py` |
| Persistence or recovery | `db.py`, `worker.py`, API tests |
| Reconstruction and point density | `reconstruction.py` |
| Desktop scan dashboard | `desktop.html`, loopback routes in `api.py` |
| Saved-scene viewing | `viewer.py`; sample showcase in `docs/benchmarks/` |

See [DESIGN.md](DESIGN.md) for data flow and [SPEC.md](SPEC.md) for expected behavior.
