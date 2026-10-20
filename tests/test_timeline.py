import json

from fastapi.testclient import TestClient
from test_api import make_client, pair
from test_streaming import frames, run, start


def test_timeline_access_and_empty_scan(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        sid = start(client, pair(client, app))
        root = f'/desktop/scans/{sid}'
        assert client.get(root + '/reconstruction-timeline').json() == {
            'frames': [], 'snapshots': [], 'comparison': None}
        remote = TestClient(app, client=('192.168.1.10', 1234))
        assert remote.get(root + '/reconstruction-timeline').status_code == 403
        assert client.get('/desktop/scans/missing/reconstruction-timeline').status_code == 404


def test_timeline_snapshots_and_timing_are_explicit(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)
        frames(client, headers, sid, 0, 12)
        first = run(app)
        second = run(app)
        root = f'/desktop/scans/{sid}'
        result = client.get(root + '/reconstruction-timeline').json()
        assert [s['job_id'] for s in result['snapshots']] == [first, second]
        assert result['snapshots'][0]['frame_ids'] == [f'f-{i:04}' for i in range(8)]
        assert all(f['status'] == 'completed' for f in result['frames'])
        assert result['frames'][4]['job_ids'] == [first, second]
        assert result['frames'][0]['video_time_source'] is None
        assert client.get(root + '/scene', params={'job_id': first}).json()['completed_job_id'] == first
        assert client.get(root + '/scene', params={'job_id': 'missing'}).status_code == 404
        assert client.get(root + '/scene/points', params={'job_id': first}).status_code == 200
        assert client.get(root + '/artifacts/cloud.ply', params={'job_id': first}).status_code == 200
        other = start(client, headers)
        assert client.get(f'/desktop/scans/{other}/scene', params={'job_id': first}).status_code == 404
        assert client.get(f'/desktop/scans/{other}/artifacts/cloud.ply',
                          params={'job_id': first}).status_code == 404

        tracking = tmp_path / 'scans' / sid / 'tracking'
        tracking.mkdir()
        session = 'a' * 32
        with app.state.db.connect() as db:
            for n in (1, 2, 3):
                fid = f'video-{session}-{n:09}'
                db.execute('INSERT INTO frames VALUES (?,?,?,?,?,?,?)',
                           (sid, fid, 'hash', f'2026-10-20T00:00:0{n}+00:00', 10, 20, 'unused'))
                if n < 3:
                    (tracking / f'{fid}.json').write_text(json.dumps(
                        {'encoder_timestamp_ns': 1000000000 + (n-1)*40000000}), encoding='utf-8')
        result = client.get(root + '/reconstruction-timeline').json()
        video = result['frames'][-3:]
        assert video[1]['capture_elapsed_seconds'] == .04
        assert video[1]['capture_time_source'] == 'encoder_pts'
        assert video[1]['video_pts_seconds'] == 1 / 30
        assert video[2]['video_time_source'] == 'nominal_remux_30fps'
        assert video[2]['capture_elapsed_seconds'] is None
        assert all(f['status'] == 'received' for f in video)
        reports = tmp_path / 'scans' / sid / 'colmap' / 'run'
        reports.mkdir(parents=True)
        (reports / 'report.json').write_text('{"status":"inconclusive"}', encoding='utf-8')
        assert client.get(root + '/reconstruction-timeline').json()['comparison']['status'] == 'inconclusive'
