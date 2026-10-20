import json

from iotdesign.worker import claim, process_job
from test_api import make_client, pair
from test_streaming import backend, start
from test_video import encoded_video


def sample(index=1):
    return {'session_id': 'c' * 32, 'video_frame': index,
            'timestamp_ns': 1000000000 + index, 'ar_timestamp_ns': 1000000000 + index,
            'tracking_state': 'TRACKING', 'camera_to_world': [1, 0, 0, 0, 0, 1, 0, 0,
                                                            0, 0, 1, 0, 0, 0, 0, 1],
            'imu': [{'sensor_type': 4, 'timestamp_ns': 1000000000,
                     'accuracy': 3, 'values': [0.1, 0.2, 0.3]}]}


def test_tracking_auth_owner_validation_idempotency_and_frozen_scan(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)
        path = f'/api/scans/{sid}/tracking'
        assert client.post(path, json=sample()).status_code == 401
        assert client.post(path, json=sample(), headers=pair(client, app)).status_code == 404
        assert client.post(path, json=sample(2), headers=headers).status_code == 200
        value = sample(); value['camera_to_world'] = None
        assert client.post(path, json=value, headers=headers).status_code == 422
        assert client.post(path, json=sample(), headers=headers).status_code == 200
        assert client.post(path, json=sample(), headers=headers).status_code == 200
        value = sample(); value['timestamp_ns'] += 1
        assert client.post(path, json=value, headers=headers).status_code == 409
        with app.state.db.connect() as db:
            db.execute("UPDATE scans SET state='frozen' WHERE id=?", (sid,))
        assert client.post(path, json=sample(61), headers=headers).status_code == 409


def test_video_frame_identity_reaches_frozen_worker_tracking_inputs(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)
        for i in range(1, 9):
            assert client.post(f'/api/scans/{sid}/tracking', json=sample(i), headers=headers).status_code == 200
        response = client.post(f'/api/scans/{sid}/video', content=encoded_video(), headers={
            **headers, 'Content-Type': 'video/h264', 'X-Video-Session': 'c' * 32})
        assert response.status_code == 200
        seen = {}

        def tracked_backend(paths, output, *args, **kwargs):
            assert kwargs['max_points'] == 0
            seen.update(kwargs['camera_tracking'])
            assert set(seen) == {p.stem for p in paths}
            return backend(paths, output, *args, **kwargs)

        job = claim(app.state.db)
        process_job(app.state.db, app.state.settings, job, tracked_backend)
        assert app.state.db.job(job['id'])['state'] == 'completed'
        assert len(seen) == 8
        path = tmp_path / 'scans' / sid / 'artifacts' / job['id'] / 'camera-tracking-inputs.json'
        assert json.loads(path.read_text()) == seen


def test_tracking_batches_keep_all_frame_ids_and_reject_conflicts_before_writes(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)
        path = f'/api/scans/{sid}/tracking'
        batch = {'records': [sample(i) for i in range(1, 7)]}
        assert client.post(path, json=batch, headers=headers).status_code == 200
        assert client.post(path, json=batch, headers=headers).status_code == 200
        folder = tmp_path / 'scans' / sid / 'tracking'
        assert len(list(folder.glob('*.json'))) == 6
        conflict = sample(1); conflict['timestamp_ns'] += 1
        assert client.post(path, json={'records': [sample(7), conflict]}, headers=headers).status_code == 409
        assert len(list(folder.glob('*.json'))) == 6
        assert client.post(path, json={'records': []}, headers=headers).status_code == 422
        assert client.post(path, json={'records': [sample(i) for i in range(1, 32)]}, headers=headers).status_code == 422


def test_camera_path_exposes_each_frame_and_rejects_bad_clock_without_invented_positions(tmp_path):
    from fastapi.testclient import TestClient
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        # Use an owned scan for the transmitting client.
        sid = start(client, headers)
        records = [sample(i) for i in range(1, 7)]
        records[2]['ar_timestamp_ns'] += 200000000
        assert client.post(f'/api/scans/{sid}/tracking', headers=headers, json={'records': records}).status_code == 200
        root = f'/desktop/scans/{sid}/camera-path'
        response = client.get(root)
        assert response.status_code == 200
        data = response.json()
        assert [r['video_frame'] for r in data['frames']] == list(range(1, 7))
        assert data['frames'][0]['synchronized']
        assert not data['frames'][2]['synchronized']
        assert data['frames'][2]['clock_offset_ms'] == 200
        assert data['frames'][0]['imu_sample_count'] == 1
        assert 'imu' not in data['frames'][0]
        assert data['frames'][0]['frame_id'].endswith('000000001')
        assert [r['video_frame'] for r in client.get(root+'?after_frame=4').json()['frames']] == [5,6]
        assert client.get(root+'?session_id=bad').status_code == 422
        assert client.get(root+'?after_frame=-1').status_code == 422
        remote = TestClient(app, client=('192.168.1.10', 1234))
        assert remote.get(root).status_code == 403
