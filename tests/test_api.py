import threading
from datetime import datetime, timezone
from io import BytesIO

from fastapi.testclient import TestClient
from iotdesign.api import create_app
from iotdesign.config import Settings
from iotdesign.worker import recover
from PIL import Image
from test_object_geometry import make_scene


def make_client(tmp_path):
    app = create_app(Settings(data=tmp_path, advertise=False))
    return TestClient(app), app


def pair(client, app):
    response = client.post("/api/pair", json={"code": app.state.pairing.code, "name": "Test phone"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def jpeg(color=(30, 100, 160)):
    output = BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format="JPEG")
    return output.getvalue()


def upload(client, headers, scan_id, frame_id, content=None, timestamp=None):
    content = jpeg() if content is None else content
    return client.put(
        f"/api/scans/{scan_id}/frames/{frame_id}", headers=headers,
        data={"timestamp": timestamp or datetime.now(timezone.utc).isoformat(), "width": "32", "height": "24"},
        files={"image": (f"{frame_id}.jpg", content, "image/jpeg")},
    )


def new_scan(client, headers):
    response = client.post("/api/scans", headers=headers, json={"name": "Test room"})
    assert response.status_code == 201
    return response.json()["id"]


def test_live_preview_auth_ownership_validation_and_freezing(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        path = f'/api/scans/{scan_id}/live'
        desktop = f'/desktop/scans/{scan_id}/live'
        content = jpeg()
        files = {'image': ('preview.jpg', content, 'image/jpeg')}
        assert client.put(path, files=files).status_code == 401
        other = pair(client, app)
        assert client.put(path, headers=other, files=files).status_code == 404
        assert client.delete(path, headers=other).status_code == 404
        assert client.get(desktop).status_code == 204
        assert client.put(path, headers=headers, files=files).status_code == 200
        assert client.get('/desktop/status').json()['live_scan_ids'] == [scan_id]
        response = client.get(desktop)
        assert response.content == content
        assert response.headers['cache-control'] == 'no-store'
        assert client.get(f'/api/scans/{scan_id}', headers=headers).json()['frames'] == []
        assert client.put(path, headers=headers,
                          files={'image': ('bad.jpg', b'bad', 'image/jpeg')}).status_code == 422
        assert client.put(path, headers=headers,
                          files={'image': ('big.jpg', b'x' * (512 * 1024 + 1), 'image/jpeg')}).status_code == 413
        wide = BytesIO()
        Image.new('RGB', (641, 10)).save(wide, format='JPEG')
        assert client.put(path, headers=headers,
                          files={'image': ('wide.jpg', wide.getvalue(), 'image/jpeg')}).status_code == 422
        assert client.delete(path, headers=headers).status_code == 204
        assert client.get('/desktop/status').json()['live_scan_ids'] == []
        assert client.get(desktop).status_code == 204
        assert client.put(path, headers=headers, files=files).status_code == 200
        with app.state.db.connect() as connection:
            connection.execute("UPDATE scans SET state='frozen' WHERE id=?", (scan_id,))
        assert client.put(path, headers=headers, files=files).status_code == 409
        assert client.get(desktop).status_code == 204


def test_live_preview_desktop_is_local_and_revocation_hides_feed(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        assert client.put(f'/api/scans/{scan_id}/live', headers=headers,
                          files={'image': ('live.jpg', jpeg(), 'image/jpeg')}).status_code == 200
        remote = TestClient(app, client=('192.168.1.50', 1234))
        assert remote.get(f'/desktop/scans/{scan_id}/live').status_code == 403
        assert client.delete('/api/pair', headers=headers).status_code == 204
        assert client.get(f'/desktop/scans/{scan_id}/live').status_code == 204
        assert client.get('/desktop/status').json()['live_scan_ids'] == []


def test_saved_photo_gallery_original_thumbnail_and_local_access(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        root = f'/desktop/scans/{scan_id}'
        assert client.get(root + '/frames').json() == []
        content = jpeg()
        assert upload(client, headers, scan_id, 'photo-1', content=content).status_code == 200
        gallery = client.get(root + '/photos')
        assert gallery.status_code == 200
        assert f'data-scan="{scan_id}"' in gallery.text
        frames = client.get(root + '/frames').json()
        assert len(frames) == 1 and frames[0]['id'] == 'photo-1'
        assert 'path' not in frames[0]
        original = client.get(root + '/frames/photo-1')
        assert original.content == content
        assert original.headers['cache-control'] == 'no-store'
        thumbnail = client.get(root + '/frames/photo-1?thumbnail=true')
        assert thumbnail.status_code == 200
        with Image.open(BytesIO(thumbnail.content)) as image:
            assert max(image.size) <= 320
        assert client.get(root + '/frames/missing').status_code == 404
        assert client.get('/desktop/scans/missing/photos').status_code == 404
        assert client.get('/desktop/scans/missing/frames').status_code == 404
        assert client.get('/desktop/status').json()['scans'][0]['frame_count'] == 1
        remote = TestClient(app, client=('192.168.1.50', 1234))
        for path in ['/photos', '/frames', '/frames/photo-1', '/frames/photo-1?thumbnail=true']:
            assert remote.get(root + path).status_code == 403
        with app.state.db.connect() as connection:
            connection.execute('UPDATE scans SET name=? WHERE id=?', ('<script>test</script>', scan_id))
        assert '<h1>&lt;script&gt;test&lt;/script&gt;</h1>' in client.get(root + '/photos').text


def test_pairing_authentication_and_health_metadata(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        health = client.get("/api/health").json()
        assert health["server_id"] == app.state.server_id
        assert health["api_version"] == 1
        assert health["fingerprint"] == app.state.fingerprint
        assert client.get("/api/scans").status_code == 401
        assert client.post("/api/pair", json={"code": "11111111"}).status_code == 401
        headers = pair(client, app)
        assert client.get("/api/scans", headers=headers).json() == []


def test_frame_upload_is_idempotent_and_rejects_conflicting_ids(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        first = upload(client, headers, scan_id, "frame-1")
        repeated = upload(client, headers, scan_id, "frame-1", timestamp=first.json()["timestamp"])
        assert first.status_code == 200
        assert repeated.json() == first.json()
        conflict = upload(client, headers, scan_id, "frame-1", content=jpeg((1, 2, 3)),
                          timestamp=first.json()["timestamp"])
        assert conflict.status_code == 409


def test_upload_rejects_invalid_jpeg_and_oversized_image(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        invalid = upload(client, headers, scan_id, "invalid", content=b"not a jpeg")
        assert invalid.status_code == 422
        assert invalid.json()["detail"] == "invalid_image"


def test_reconstruction_freezes_scan_after_eight_frames(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        for index in range(8):
            assert upload(client, headers, scan_id, f"frame-{index}").status_code == 200
        too_few = client.post(f"/api/scans/{scan_id}/reconstruct", headers=headers,
                              json={"frame_count": 12})
        assert too_few.status_code == 202
        late_frame = upload(client, headers, scan_id, "late-frame")
        assert late_frame.status_code == 409
        assert late_frame.json()["detail"] == "scan_frozen"


def test_worker_recovery_marks_running_jobs_failed_and_preserves_images(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        for index in range(8):
            assert upload(client, headers, scan_id, f"frame-{index}").status_code == 200
        job = client.post(f"/api/scans/{scan_id}/reconstruct", headers=headers,
                          json={"frame_count": 8}).json()
    with app.state.db.connect() as db:
        db.execute("UPDATE jobs SET state='running',stage='inference' WHERE id=?", (job["id"],))
    recover(app.state.db)
    recovered = app.state.db.job(job["id"])
    assert recovered["state"] == "failed"
    assert recovered["stage"] == "interrupted"
    with app.state.db.connect() as db:
        paths = list(db.execute("SELECT path FROM frames WHERE scan_id=?", (scan_id,)))
    assert len(paths) == 8
    assert all((tmp_path / row[0]).is_file() for row in paths)


def test_desktop_can_retry_a_failed_reconstruction_without_recapturing(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        for index in range(8):
            assert upload(client, headers, scan_id, f"frame-{index}").status_code == 200
        original = client.post(f"/api/scans/{scan_id}/reconstruct", headers=headers,
                               json={"frame_count": 8}).json()
        with app.state.db.connect() as db:
            db.execute("UPDATE jobs SET state='failed',stage='failed' WHERE id=?", (original["id"],))
        retry = client.post(f"/desktop/scans/{scan_id}/retry")
        assert retry.status_code == 200
        assert retry.json()["id"] != original["id"]
        assert retry.json()["state"] == "queued"
        assert retry.json()["frame_count"] == 8


def test_segmentation_artifacts_require_scan_ownership_and_missing_files_return_404(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        for index in range(8):
            upload(client, headers, scan_id, f'frame-{index}')
        job = client.post(f'/api/scans/{scan_id}/reconstruct', headers=headers,
                          json={'frame_count': 8}).json()
        folder = tmp_path / 'scans' / scan_id / 'artifacts' / job['id']
        folder.mkdir(parents=True)
        (folder / 'objects.json').write_text('{"id_scope":"per_frame"}', encoding='utf-8')
        with app.state.db.connect() as db:
            db.execute("UPDATE jobs SET state='completed',artifact_dir=? WHERE id=?",
                       (str(folder.relative_to(tmp_path)), job['id']))
        url = f'/api/scans/{scan_id}/artifacts/'
        assert client.get(url + 'objects.json', headers=headers).status_code == 200
        assert client.get(url + 'objects.json').status_code == 401
        other_headers = pair(client, app)
        assert client.get(url + 'objects.json', headers=other_headers).status_code == 404
        assert client.get(url + 'objects.npz', headers=headers).status_code == 404
        assert client.get(url + 'arbitrary-file', headers=headers).status_code == 404


def test_object_review_authentication_revision_and_restore(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = new_scan(client, headers)
        for index in range(8):
            upload(client, headers, scan_id, f'frame-{index}')
        job = client.post(f'/api/scans/{scan_id}/reconstruct', headers=headers,
                          json={'frame_count': 8}).json()
        folder = tmp_path / 'scans' / scan_id / 'artifacts' / job['id']
        object_id = make_scene(folder)
        with app.state.db.connect() as db:
            db.execute("UPDATE jobs SET state='completed',artifact_dir=? WHERE id=?",
                       (str(folder.relative_to(tmp_path)), job['id']))
        url = f'/api/scans/{scan_id}/objects'
        assert client.get(url, headers=headers).json()['review']['revision'] == 0
        body = {'excluded_ids': [object_id], 'expected_revision': 0}
        assert client.post(url + '/review', json=body).status_code == 401
        other = pair(client, app)
        assert client.post(url + '/review', headers=other, json=body).status_code == 404
        assert client.post(url + '/review', headers=headers,
                           json={'excluded_ids': ['unknown'], 'expected_revision': 0}).status_code == 422
        reviewed = client.post(url + '/review', headers=headers, json=body)
        assert reviewed.json()['point_count_after'] == 2
        assert client.post(url + '/review', headers=headers, json=body).status_code == 409
        restored = client.post(url + '/review', headers=headers,
                               json={'excluded_ids': [], 'expected_revision': 1})
        assert restored.json()['point_count_after'] == 4
        assert app.state.db.job(job['id'])['metrics']['point_count'] == 4
        assert client.get(f'/desktop/scans/{scan_id}/review').status_code == 200
        assert client.get(f'/desktop/scans/{scan_id}/objects/masks/{object_id}').headers[
            'content-type'] == 'image/png'
        assert client.post(f'/desktop/scans/{scan_id}/objects/review',
                           headers={'Origin': 'https://unrelated.example'},
                           json={'excluded_ids': [], 'expected_revision': 2}).status_code == 403


def test_mdns_lifecycle_runs_outside_async_event_loop(tmp_path, monkeypatch):
    events = []

    class Advertisement:
        def __init__(self, *args):
            events.append(('start', threading.get_ident()))

        def close(self):
            events.append(('close', threading.get_ident()))

    monkeypatch.setattr('iotdesign.api.Advertisement', Advertisement)
    app = create_app(Settings(data=tmp_path, advertise=True))

    @app.get('/loop-thread')
    async def loop_thread():
        return {'thread': threading.get_ident()}

    with TestClient(app) as client:
        loop_id = client.get('/loop-thread').json()['thread']
        assert events[0][0] == 'start' and events[0][1] != loop_id
    assert events[1][0] == 'close' and events[1][1] != loop_id
