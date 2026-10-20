# Repository instructions

## Start here on every fresh session

Read [REMINDERS.md](REMINDERS.md) first, before planning or changing this project. Review its priority tasks and unfinished validation, then continue the highest-priority work consistent with the user's current request. Verify live state rather than assuming previously running services or installed builds are unchanged. Update that file after meaningful progress or before handing off, keeping completed work, pending tasks and unverified claims separate. The user's latest instructions take precedence over this handoff.

IoTDesign captures room images in a native mobile app, sends them to a local PC, and reconstructs an unscaled colored point cloud with official VGGT.

## Read the relevant document

- Before changing behavior or acceptance criteria, read [SPEC.md](SPEC.md).
- Before changing pairing, storage, jobs, reconstruction, or viewing, read [DESIGN.md](DESIGN.md).
- To locate implementation files, read [Project-Structure.md](Project-Structure.md).
- Before working in `mobile/`, read [mobile/AGENTS.md](mobile/AGENTS.md). Its Expo and native-generation instructions apply there.
- Use [README.md](README.md) for setup and [GATES.md](GATES.md) for recorded acceptance evidence.

## Development rules

- Preserve authenticated HTTPS, certificate pinning, stable server identity, and scan ownership. Discovery finds candidates; pairing establishes trust.
- Preserve original images, acknowledged frame IDs, and durable job records. Keep uploads idempotent and freeze a scan before reconstruction.
- Run one GPU worker per data directory. The current worker and its OS lock are Windows-specific; a WSL2 worker is a future migration.
- Keep geometry labeled unscaled until a measurement establishes physical dimensions. Describe outputs as point clouds; distinguish them from meshes, Gaussian splats, and animated human models.
- Treat emulator captures and the Pexels benchmark as limited test evidence. Record real-device and real-room acceptance separately.
- Keep credentials, pairing codes, certificates, scan images, model weights, and generated build outputs in local runtime storage. Document examples with placeholders.
- Update the relevant specification, design notes, and validation evidence when implementation behavior changes.

## Documentation lookup

Use Context7 for current third-party library, SDK, API, and CLI documentation. First resolve the library ID, then query that ID for one concept at a time. Use the installed version where available. For Expo work, also follow the matching-version documentation requirement in `mobile/AGENTS.md`.

## Validation

For server behavior changes, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check server tests
```

For mobile changes, run the applicable scripts in `mobile/package.json`, including tests and type checking. Follow `mobile/AGENTS.md` for linting and native builds. Use a development build for the custom discovery/HTTPS module; Expo Go cannot validate that module.

For documentation-only changes, check local links and claims against source; code tests are unnecessary. Report which checks were actually run and any remaining device-validation limits.
