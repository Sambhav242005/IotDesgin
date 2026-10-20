from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi.testclient import TestClient
from iotdesign.reconstruction import export_scene
from iotdesign.worker import claim, process_job, recover
from test_api import make_client, pair, upload


def start(client, headers):
    response = client.post('/api/scans', headers=headers,
                           json={'name': 'Stream room', 'capture_mode': 'stream'})
    assert response.status_code == 201
    assert response.json()['capture_mode'] == 'stream'
    return response.json()['id']


def frames(client, headers, scan_id, first, end):
    for i in range(first, end):
        timestamp = (datetime(2026, 10, 18, tzinfo=timezone.utc) + timedelta(seconds=i)).isoformat()
        assert upload(client, headers, scan_id, f'f-{i:04}', timestamp=timestamp).status_code == 200


def backend(paths, output, scan_id, job_id, **kwargs):
    assert all(path.is_file() for path in paths)
    return export_scene(output, scan_id, job_id, np.array([[0, 0, 1], [1, 0, 2]]),
                        np.array([[255, 0, 0], [0, 255, 0]]), [], {'input_frames': len(paths)},
                        np.zeros((2, 2, 3), dtype=np.uint8))


def run(app, seen=None):
    job = claim(app.state.db)
    assert job is not None

    def record(paths, *args, **kwargs):
        if seen is not None:
            seen.append([path.stem for path in paths])
        return backend(paths, *args, **kwargs)

    process_job(app.state.db, app.state.settings, job, record, segment_objects=True)
    assert app.state.db.job(job['id'])['state'] == 'completed'
    return job['id']


def test_stream_snapshots_keep_inputs_frozen_and_coalesce_new_frames(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        frames(client, headers, scan_id, 0, 7)
        assert claim(app.state.db) is None
        frames(client, headers, scan_id, 7, 8)
        frames(client, headers, scan_id, 8, 35)
        seen = []
        first_job = run(app, seen)
        assert seen[0] == [f'f-{i:04}' for i in range(8)]
        assert client.get(f'/api/scans/{scan_id}', headers=headers).json()['state'] == 'capturing'
        status = client.get(f'/desktop/scans/{scan_id}/scene').json()
        assert status['completed_job_id'] == first_job
        assert status['latest_job']['state'] == 'queued'
        run(app, seen)
        assert seen[1] == [f'f-{i:04}' for i in range(4, 12)]
        while claimable(app):
            run(app, seen)
        # A short tail waits until Finish; no input gets discarded.
        assert client.post(f'/api/scans/{scan_id}/stream/finish', headers=headers).status_code == 200
        while claimable(app):
            run(app, seen)
        assert set().union(*(set(batch) for batch in seen)) == {f'f-{i:04}' for i in range(35)}
        assert claim(app.state.db) is None
        assert len(list((tmp_path / 'scans' / scan_id / 'images').glob('*.jpg'))) == 35


def test_finish_waits_for_active_job_and_includes_final_partial_batch(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        frames(client, headers, scan_id, 0, 10)
        path = f'/api/scans/{scan_id}/stream/finish'
        assert client.post(path, headers=headers).status_code == 200
        assert upload(client, headers, scan_id, 'late').status_code == 409
        run(app)
        scan = client.get(f'/api/scans/{scan_id}', headers=headers).json()
        assert scan['state'] == 'frozen'
        run(app)
        assert client.get(f'/api/scans/{scan_id}', headers=headers).json()['state'] == 'completed'
        assert client.post(path, headers=headers).status_code == 200
        assert client.get(f'/api/scans/{scan_id}', headers=headers).json()['state'] == 'completed'
        assert claim(app.state.db) is None


def test_live_scene_points_are_job_bound_and_loopback_only(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        frames(client, headers, scan_id, 0, 8)
        first = run(app)
        frames(client, headers, scan_id, 8, 12)
        second = run(app)
        root = f'/desktop/scans/{scan_id}'
        for job_id in (first, second):
            response = client.get(root + '/scene/points', params={'job_id': job_id})
            assert response.status_code == 200
            values = np.frombuffer(response.content, dtype='<f4').reshape(-1, 6)
            np.testing.assert_array_equal(values[:, :3], [[0, 0, 1], [1, 0, 2]])
            assert response.headers['cache-control'] == 'no-store'
        assert client.get(root + '/scene/cameras', params={'job_id': first}).json() == []
        other = start(client, headers)
        assert client.get(f'/desktop/scans/{other}/scene/points', params={'job_id': first}).status_code == 404
        remote = TestClient(app, client=('192.168.1.10', 1234))
        for suffix in ('/scene', '/scene/points?job_id=' + first, '/scene/cameras?job_id=' + first, '/view'):
            assert remote.get(root + suffix).status_code == 403
        assert 'Live room reconstruction' in client.get(root + '/view').text
        assert client.get(f'/api/scans/{scan_id}/scene', headers=pair(client, app)).status_code == 404


def test_stream_finish_auth_idempotency_and_recovery_retry(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        finish = f'/api/scans/{scan_id}/stream/finish'
        assert client.post(finish).status_code == 401
        assert client.post(finish, headers=pair(client, app)).status_code == 404
        assert client.post(finish, headers=headers).status_code == 422
        frames(client, headers, scan_id, 0, 8)
        job = claim(app.state.db)
        recover(app.state.db)
        assert app.state.db.job(job['id'])['state'] == 'failed'
        response = client.post(f'/desktop/scans/{scan_id}/retry')
        assert response.status_code == 200 and response.json()['state'] == 'queued'
        assert response.json()['id'] != job['id']
        run(app)
        assert client.post(finish, headers=headers).status_code == 200
        assert client.get(f'/api/scans/{scan_id}', headers=headers).json()['state'] == 'completed'


def test_stream_upload_limit_preserves_idempotent_acknowledgment(tmp_path, monkeypatch):
    monkeypatch.setattr('iotdesign.api.STREAM_FRAME_LIMIT', 8)
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        frames(client, headers, scan_id, 0, 8)
        # Lost acknowledgments can be retried even after the scan reaches its limit.
        timestamp = datetime(2026, 10, 18, tzinfo=timezone.utc).isoformat()
        assert upload(client, headers, scan_id, 'f-0000', timestamp=timestamp).status_code == 200
        conflict = upload(client, headers, scan_id, 'f-0000', content=b'not a JPEG', timestamp=timestamp)
        assert conflict.status_code == 422
        assert upload(client, headers, scan_id, 'over-limit').status_code == 409
        with app.state.db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM frames WHERE scan_id=?', (scan_id,)).fetchone()[0] == 8


def claimable(app):
    with app.state.db.connect() as db:
        return db.execute("SELECT 1 FROM jobs WHERE state='queued'").fetchone() is not None


def test_failed_batch_blocks_new_frames_until_exact_retry(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)
        frames(client, headers, sid, 0, 8)
        first = claim(app.state.db)
        recover(app.state.db)
        frames(client, headers, sid, 8, 20)
        assert claim(app.state.db) is None
        response = client.post(f'/desktop/scans/{sid}/retry')
        assert response.status_code == 200
        seen = []
        run(app, seen)
        assert seen == [[f'f-{i:04}' for i in range(8)]]
        assert app.state.db.job(first['id'])['state'] == 'failed'
        run(app, seen)
        assert seen[1] == [f'f-{i:04}' for i in range(4, 12)]
