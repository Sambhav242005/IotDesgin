import json

import numpy as np
from iotdesign.reconstruction import export_scene
from iotdesign.worker import claim, process_job
from test_api import make_client, pair
from test_streaming import frames, start


def test_tracked_batches_accumulate_and_new_origins_stay_separate(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)

        def run(first, group):
            job = claim(app.state.db)

            def backend(paths, output, scan_id, job_id, **kwargs):
                points = np.array([[first, 0, 0], [first + 1, 0, 0]], dtype=np.float32)
                camera = {'frame': paths[0].stem, 'camera_to_world': np.eye(4).tolist()}
                return export_scene(output, scan_id, job_id, points, np.full((2, 3), 120), [camera],
                                    {'input_frames': len(paths), 'camera_tracking': {'status': 'arcore_aligned', 'session_id': group}},
                                    np.zeros((2, 2, 3), dtype=np.uint8),
                                    provenance={'frame_index': np.zeros(2, dtype=int),
                                                'source_uv': np.full((2, 2), 0.5)})

            process_job(app.state.db, app.state.settings, job, backend)
            assert app.state.db.job(job['id'])['state'] == 'completed'
            return tmp_path / 'scans' / sid / 'artifacts' / job['id']

        frames(client, headers, sid, 0, 8)
        first = run(0, 'a')
        assert np.load(first / 'viewer.npz')['points'].shape == (2, 3)
        frames(client, headers, sid, 8, 12)
        second = run(1, 'a')
        with np.load(second / 'viewer.npz') as data:
            assert np.array_equal(data['points'][:, 0], [0, 1, 2])
            assert data['frame_index'].tolist() == [0, 0, 1]
        assert np.load(second / 'snapshot-viewer.npz')['points'].shape == (2, 3)
        assert json.loads((second / 'scene.json').read_text())['metrics']['live_map']['batches'] == 2
        frames(client, headers, sid, 12, 16)
        third = run(10, 'b')
        assert np.load(third / 'viewer.npz')['points'].shape == (2, 3)


def test_desktop_binary_contains_all_points_above_old_render_cap(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        sid = start(client, pair(client, app))
        with app.state.db.connect() as db:
            db.execute("INSERT INTO jobs(id,scan_id,state,stage,frame_count,created_at,updated_at,artifact_dir) "
                       "VALUES ('full',?,'completed','done',8,'now','now','scene')", (sid,))
        output = tmp_path / 'scene'
        output.mkdir()
        count = 100123
        points = np.arange(count * 3, dtype=np.float32).reshape(-1, 3)
        np.savez(output / 'viewer.npz', points=points, colors=np.zeros((count, 3), dtype=np.uint8))
        response = client.get(f'/desktop/scans/{sid}/scene/points?job_id=full')
        assert response.status_code == 200
        payload = np.frombuffer(response.content, dtype='<f4').reshape(-1, 6)
        assert len(payload) == count
        assert np.array_equal(payload[:, :3], points)
