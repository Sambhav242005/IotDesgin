import argparse
import json
import logging
import socket
from pathlib import Path

from .config import Settings
from .db import Database


def main():
    parser = argparse.ArgumentParser(description='IoTDesign Windows capture station')
    parser.add_argument('--data', type=Path, default=Path('data'))
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8443)
    serve.add_argument('--name', default=socket.gethostname())
    serve.add_argument('--no-discovery', action='store_true')
    worker = commands.add_parser('worker')
    worker.add_argument('--repo', type=Path, default=Path('vendor/vggt'))
    worker.add_argument('--resolution', type=int, default=336)
    worker.add_argument('--once', action='store_true')
    worker.add_argument('--segment-objects', action='store_true', help='Run optional SAM 2 after VGGT')
    worker.add_argument('--semantic-objects', action='store_true', help='YOLO names and SAM 2 masks')
    worker.add_argument('--max-frames', type=int, choices=(8, 12, 20), default=20)
    worker.add_argument('--weight-precision', choices=('fp32', 'fp16'), default='fp32')
    worker.add_argument('--max-points', type=int, default=500000)
    worker.add_argument('--sam-model', default='facebook/sam2.1-hiera-small')
    worker.add_argument('--segmentation-edge', type=int, default=1024)
    worker.add_argument('--vram-budget-gb', type=float, default=0)
    segment = commands.add_parser('segment', help='Create SAM 2 masks from JPEG images')
    segment.add_argument('--images', type=Path, required=True)
    segment.add_argument('--output', type=Path, required=True)
    segment.add_argument('--count', type=int, default=12)
    segment.add_argument('--semantic', action='store_true', help='Prompt SAM 2 with mirror-capable YOLO')
    view = commands.add_parser('view')
    view.add_argument('--scan', required=True)
    view.add_argument('--port', type=int, default=8080)
    benchmark = commands.add_parser('benchmark')
    benchmark.add_argument('--images', type=Path, required=True)
    benchmark.add_argument('--repo', type=Path, default=Path('vendor/vggt'))
    benchmark.add_argument('--resolution', type=int, default=336)
    benchmark.add_argument('--output', type=Path, default=Path('data/benchmark'))
    commands.add_parser('doctor')
    export = commands.add_parser('openapi')
    export.add_argument('--output', type=Path, default=Path('docs/openapi.json'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    settings = Settings(data=args.data)
    if args.command == 'serve':
        import uvicorn

        from .api import create_app
        from .security import ensure_certificate
        settings.port, settings.name, settings.advertise = args.port, args.name, not args.no_discovery
        app = create_app(settings)
        cert, key, _ = ensure_certificate(settings.data)
        print(f'Desktop station: https://127.0.0.1:{args.port}\nPairing code is shown on the desktop station.', flush=True)
        uvicorn.run(app, host=settings.host, port=settings.port, ssl_certfile=str(cert), ssl_keyfile=str(key))
    elif args.command == 'worker':
        from .worker import run_worker
        run_worker(settings, args.repo, args.resolution, args.once,
                   args.segment_objects or args.semantic_objects, args.semantic_objects,
                   args.max_frames, args.weight_precision, args.max_points,
                   args.sam_model, args.segmentation_edge, args.vram_budget_gb)
    elif args.command == 'segment':
        from .reconstruction import evenly_spaced
        from .segmentation import segment_frames
        from .worker import worker_lock
        paths = sorted(args.images.glob('*.jpg'))
        if not paths or args.count < 1 or args.count > 30:
            parser.error('Segmentation requires JPEG images and a count between 1 and 30')
        with worker_lock(settings.data):
            metrics = segment_frames(evenly_spaced(paths, args.count), args.output,
                                     stage=lambda value: print(value, flush=True), semantic=args.semantic,
                                     detector_path=settings.data / 'models' / 'yolov8n-oiv7.pt')
        print(json.dumps(metrics, indent=2))
    elif args.command == 'view':
        from .viewer import run_viewer
        with Database(settings.db_path).connect() as db:
            row = db.execute("SELECT artifact_dir FROM jobs WHERE scan_id=? AND state='completed' "
                             "ORDER BY updated_at DESC LIMIT 1", (args.scan,)).fetchone()
        if not row:
            parser.error('No completed scene for this scan')
        run_viewer(settings.data / row[0], args.port)
    elif args.command == 'benchmark':
        from .reconstruction import reconstruct
        from .worker import worker_lock
        paths = sorted(args.images.glob('*.jpg'))
        if len(paths) < 20:
            parser.error('Benchmark requires at least 20 JPEG images for genuine 8/12/20-frame comparisons')
        args.output.mkdir(parents=True, exist_ok=True)
        results = []
        with worker_lock(settings.data):
            for count in (8, 12, 20):
                try:
                    metrics = reconstruct(paths, args.output / str(count), 'benchmark', str(count), count,
                                          repo=args.repo, resolution=args.resolution, retry=False,
                                          stage=lambda s: print(s, flush=True))
                    results.append({'requested_frames': count, 'state': 'completed', **metrics})
                except Exception as exc:  # noqa: BLE001 - record one failed benchmark and continue to the next size.
                    results.append({'requested_frames': count, 'state': 'failed', 'error': str(exc)})
                (args.output / 'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
        if any(r['state'] == 'failed' for r in results):
            raise SystemExit(1)
    elif args.command == 'doctor':
        import importlib.util
        print('Windows-native server: ready')
        print('VGGT checkout:', Path('vendor/vggt/vggt/models/vggt.py').exists())
        if importlib.util.find_spec('torch'):
            import torch
            print('PyTorch:', torch.__version__, 'CUDA:', torch.cuda.is_available())
            if torch.cuda.is_available():
                print('GPU:', torch.cuda.get_device_name(0))
        else:
            print('GPU dependencies not installed. Run scripts/setup-windows.ps1 -Gpu')
    elif args.command == 'openapi':
        from .api import create_app
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(create_app(Settings(data=args.data, advertise=False)).openapi(), indent=2))


if __name__ == '__main__':
    main()
