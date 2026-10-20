import io
from fractions import Fraction

import av
import numpy as np
from iotdesign.live import LivePreviews
from iotdesign.video import VideoReceiver
from test_api import make_client, pair
from test_streaming import start


def encoded_video(count=480):
    """Actual inter-frame H.264, not concatenated JPEGs."""
    output = io.BytesIO()
    container = av.open(output, 'w', format='h264')
    stream = container.add_stream('libx264', rate=30)
    stream.width, stream.height, stream.pix_fmt = 160, 96, 'yuv420p'
    stream.options = {'preset': 'ultrafast', 'tune': 'zerolatency', 'g': '30'}
    for i in range(count):
        pixels = np.full((96, 160, 3), i % 255, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
        frame.pts, frame.time_base = i, Fraction(1, 30)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return output.getvalue()


def test_continuous_h264_is_preserved_decoded_and_schedules_reconstruction(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        source = encoded_video()
        # Deliberately split NAL headers and pictures across arbitrary HTTP chunks.
        chunks = (source[i:i + 137] for i in range(0, len(source), 137))
        response = client.post(f'/api/scans/{scan_id}/video', content=chunks,
                               headers={**headers, 'Content-Type': 'video/h264'})
        assert response.status_code == 200, response.text
        assert response.json()['decoded_frames'] == 480
        assert response.json()['reconstruction_views'] == 480
        folder = tmp_path / 'scans' / scan_id
        assert next((folder / 'video').glob('*.h264')).read_bytes() == source
        assert len(list((folder / 'images').glob('*.jpg'))) == 480
        with app.state.db.connect() as db:
            job = db.execute('SELECT * FROM jobs WHERE scan_id=?', (scan_id,)).fetchone()
            assert job['state'] == 'queued' and job['frame_count'] == 8


def test_video_requires_auth_owner_stream_mode_and_valid_h264(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        path = f'/api/scans/{scan_id}/video'
        assert client.post(path, content=b'x').status_code == 401
        foreign = pair(client, app)
        video_headers = {**foreign, 'Content-Type': 'video/h264'}
        assert client.post(path, content=b'x', headers=video_headers).status_code == 404
        assert client.post(path, content=b'x', headers=headers).status_code == 415
        video_headers = {**headers, 'Content-Type': 'video/h264'}
        assert client.post(path, content=b'invalid', headers=video_headers).status_code == 422
        # Failed decoder must release the active-session slot.
        assert client.post(path, content=encoded_video(3), headers=video_headers).status_code == 200
        with app.state.db.connect() as db:
            db.execute("UPDATE scans SET state='frozen' WHERE id=?", (scan_id,))
        assert client.post(path, content=b'x', headers=video_headers).status_code == 409


def test_decoder_schedules_while_stream_is_open_across_partial_nals(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        scan_id = start(client, headers)
        with app.state.db.connect() as db:
            client_id = db.execute('SELECT client_id FROM scans WHERE id=?', (scan_id,)).fetchone()[0]
        previews = LivePreviews()
        receiver = VideoReceiver(app.state.db, app.state.settings, previews, scan_id, client_id)
        source = encoded_video()
        try:
            for offset in range(0, len(source), 137):
                receiver.feed(source[offset:offset + 137])
            # Decoder and scheduler progress before EOF or upload completion.
            assert not receiver.closed and receiver.decoded >= 470
            assert receiver.saved == receiver.decoded
            assert previews.get(scan_id) is not None
            with app.state.db.connect() as db:
                assert db.execute('SELECT state FROM jobs WHERE scan_id=?', (scan_id,)).fetchone()[0] == 'queued'
            receiver.finish(True)
            assert receiver.decoded == 480
            assert receiver.path.read_bytes() == source
        finally:
            receiver.finish()


def test_every_decoded_frame_reaches_reconstruction_without_backlog_skips(tmp_path):
    from iotdesign.worker import claim, process_job
    from test_streaming import backend
    client, app = make_client(tmp_path)
    with client:
        headers = pair(client, app)
        sid = start(client, headers)
        response = client.post(f'/api/scans/{sid}/video', content=encoded_video(61),
                               headers={**headers, 'Content-Type': 'video/h264'})
        assert response.status_code == 200
        assert response.json()['reconstruction_views'] == 61
        assert client.post(f'/api/scans/{sid}/stream/finish', headers=headers).status_code == 200
        seen = set()
        batches = 0
        def record(paths, *args, **kwargs):
            nonlocal batches
            assert kwargs['count'] == len(paths) <= 8
            assert kwargs['retry'] is False
            batches += 1
            seen.update(p.stem for p in paths)
            return backend(paths, *args, **kwargs)
        while (job := claim(app.state.db)) is not None:
            process_job(app.state.db, app.state.settings, job, record, max_frames=8)
            assert app.state.db.job(job['id'])['state'] == 'completed'
        assert len(seen) == 61
        assert batches > 1
        assert client.get(f'/api/scans/{sid}', headers=headers).json()['state'] == 'completed'
        progress = client.get(f'/desktop/scans/{sid}/scene').json()
        assert progress['received_frames'] == progress['processed_frames'] == 61
        assert progress['pending_frames'] == 0
        summary = client.get(f'/api/scans/{sid}?include_frames=false', headers=headers).json()
        assert summary['frames'] == [] and summary['frame_count'] == 61
