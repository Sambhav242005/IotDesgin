import time

import av
import pytest
from fastapi.testclient import TestClient
from iotdesign.playback import VideoPlayback, ffmpeg_path
from test_api import make_client, pair
from test_streaming import start
from test_video import encoded_video


@pytest.mark.skipif(not ffmpeg_path(), reason='Browser playback requires FFmpeg')
def test_browser_video_keeps_all_pictures_and_portrait_metadata(tmp_path):
    client, app = make_client(tmp_path)
    with client:
        sid = start(client, pair(client, app))
        source = tmp_path / 'scans' / sid / 'video' / ('a' * 32 + '.h264')
        source.parent.mkdir(parents=True)
        playback = VideoPlayback(source, rotation=90)
        try:
            playback.feed(encoded_video(91))
            deadline = time.monotonic() + 5
            while not (playback.folder / 'index.m3u8').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            # Real media is available before the phone finishes its video request.
            assert (playback.folder / 'index.m3u8').exists()
        finally:
            playback.close()
        assert playback.error is None
        root = f'/desktop/scans/{sid}'
        sessions = client.get(root + '/recordings').json()
        assert sessions[0]['rotation'] == 90 and sessions[0]['complete']
        assert sessions[0]['ready']
        index = root + '/recordings/' + 'a' * 32 + '/index.m3u8'
        response = client.get(index)
        assert response.status_code == 200
        assert '#EXT-X-ENDLIST' in response.text
        assert '#EXT-X-MAP:URI="init.mp4"' in response.text
        segments = sorted(playback.folder.glob('segment-*.m4s'))
        assert len(segments) >= 3
        # Decode fMP4 fragments and compare picture count with the encoded source.
        combined = tmp_path / 'complete.mp4'
        combined.write_bytes((playback.folder / 'init.mp4').read_bytes() + b''.join(p.read_bytes() for p in segments))
        with av.open(str(combined)) as video:
            assert sum(1 for _ in video.decode(video=0)) == 91
        assert client.get(root + '/player').status_code == 200
        remote = TestClient(app, client=('192.168.1.10', 4321))
        assert remote.get(index).status_code == 403
        assert remote.get(root + '/recordings').status_code == 403
        assert remote.get(root + '/player').status_code == 403
        assert client.get(root + '/recordings/not-a-session/index.m3u8').status_code == 404
        assert client.get(root + '/recordings/' + 'a' * 32 + '/ffmpeg.log').status_code == 404


def test_missing_ffmpeg_preserves_explicit_playback_error(tmp_path, monkeypatch):
    monkeypatch.setattr('iotdesign.playback.ffmpeg_path', lambda: None)
    playback = VideoPlayback(tmp_path / ('b' * 32 + '.h264'))
    playback.feed(b'example source')
    playback.close()
    assert 'FFmpeg' in playback.error
