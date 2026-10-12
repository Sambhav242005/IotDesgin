"""Submit local JPEGs to the loopback development server and record a real worker job."""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/benchmark/semantic-validation.json'))
    args = parser.parse_args()
    images = sorted(args.images.glob('*.jpg'))[:30]
    if len(images) < 12:
        parser.error('Use at least 12 JPEG images')
    # The local test server uses its own self-signed certificate. Production mobile transport stays pinned.
    with httpx.Client(base_url='https://127.0.0.1:8443', verify=False, timeout=45) as client:
        client.post('/desktop/pairing').raise_for_status()
        station = client.get('/desktop/status').json()
        paired = client.post('/api/pair', json={'code': station['code'], 'name': 'Semantic validation'})
        paired.raise_for_status()
        headers = {'Authorization': f"Bearer {paired.json()['token']}"}
        response = client.post('/api/scans', headers=headers, json={'name': 'Pexels semantic mask validation'})
        response.raise_for_status()
        scan_id = response.json()['id']
        started = datetime.now(timezone.utc)
        for index, path in enumerate(images):
            with Image.open(path) as image:
                width, height = image.size
            with path.open('rb') as handle:
                response = client.put(f'/api/scans/{scan_id}/frames/{path.stem}', headers=headers,
                                      data={'timestamp': (started + timedelta(seconds=index)).isoformat(),
                                            'width': str(width), 'height': str(height)},
                                      files={'image': (path.name, handle, 'image/jpeg')})
                response.raise_for_status()
        response = client.post(f'/api/scans/{scan_id}/reconstruct', headers=headers, json={'frame_count': 12})
        response.raise_for_status()
        job_id = response.json()['id']
        print(f'Scan {scan_id}; job {job_id}', flush=True)
        deadline, previous = time.monotonic() + 300, None
        while time.monotonic() < deadline:
            response = client.get(f'/api/jobs/{job_id}', headers=headers)
            response.raise_for_status()
            job = response.json()
            state = (job['state'], job['stage'])
            if state != previous:
                print(' / '.join(state), flush=True)
                previous = state
            if job['state'] in {'completed', 'failed'}:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps({'scan_id': scan_id, 'job': job}, indent=2), encoding='utf-8')
                if job['state'] != 'completed':
                    raise RuntimeError(job['error'])
                print(json.dumps(job['metrics'], indent=2), flush=True)
                return
            time.sleep(2)
        raise TimeoutError('Worker did not finish within five minutes; scan and job remain persisted')


if __name__ == '__main__':
    main()
