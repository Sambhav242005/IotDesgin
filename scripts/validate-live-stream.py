"""Run two real VGGT snapshots through the stream API using an existing JPEG fixture.

Uses isolated runtime data and the installation's GPU lock. Requires dev dependencies.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from iotdesign.api import create_app
from iotdesign.config import Settings
from iotdesign.reconstruction import reconstruct
from iotdesign.worker import claim, process_job, worker_lock
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('vendor/vggt'))
    parser.add_argument('--lock-data', type=Path, default=Path('data'))
    args = parser.parse_args()
    images = sorted(args.images.glob('*.jpg'))[:12]
    if len(images) < 12:
        parser.error('At least 12 JPEGs are required')
    args.output.mkdir(parents=True, exist_ok=False)
    settings = Settings(data=args.output, advertise=False)
    app = create_app(settings)
    reports = []
    with worker_lock(args.lock_data), TestClient(app) as client:
        response = client.post('/api/pair', json={'code': app.state.pairing.code})
        response.raise_for_status()
        headers = {'Authorization': 'Bearer ' + response.json()['token']}
        response = client.post('/api/scans', headers=headers,
                               json={'name': 'Stream validation fixture', 'capture_mode': 'stream'})
        response.raise_for_status()
        scan_id = response.json()['id']

        def send(index):
            with Image.open(images[index]) as image:
                width, height = image.size
            response = client.put(f'/api/scans/{scan_id}/frames/fixture-{index:03}', headers=headers,
                                  data={'width': width, 'height': height,
                                        'timestamp': (datetime(2026, 10, 18, tzinfo=timezone.utc)
                                                      + timedelta(seconds=index)).isoformat()},
                                  files={'image': (images[index].name, images[index].read_bytes(), 'image/jpeg')})
            response.raise_for_status()

        for i in range(8):
            send(i)

        for iteration in range(2):
            job = claim(app.state.db)
            if job is None:
                raise RuntimeError('Expected a queued snapshot')
            if iteration == 0:
                # Arrive after claim: this must not mutate the active 8-frame snapshot.
                for i in range(8, 12):
                    send(i)
            started = perf_counter()
            process_job(app.state.db, settings, job, reconstruct, repo=args.repo, resolution=336)
            result = app.state.db.job(job['id'])
            if result['state'] != 'completed':
                raise RuntimeError(result['error'])
            if result['metrics']['input_frames'] != (8 if iteration == 0 else 12):
                raise RuntimeError('Snapshot inputs changed')
            status = client.get(f'/desktop/scans/{scan_id}/scene').json()
            if status['completed_job_id'] != job['id']:
                raise RuntimeError('Latest scene was not published')
            points = client.get(f'/desktop/scans/{scan_id}/scene/points', params={'job_id': job['id']})
            points.raise_for_status()
            reports.append({'job_id': job['id'], 'wall_seconds': perf_counter() - started,
                            'metrics': result['metrics'], 'browser_payload_bytes': len(points.content)})
        response = client.post(f'/api/scans/{scan_id}/stream/finish', headers=headers)
        response.raise_for_status()
        if client.get(f'/api/scans/{scan_id}', headers=headers).json()['state'] != 'completed':
            raise RuntimeError('Finished stream did not complete')
    report = {'fixture': str(args.images), 'scan_id': scan_id, 'snapshots': reports,
              'limitation': 'Offline fixture replay; not a physical-phone or real-room quality test.'}
    (args.output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
