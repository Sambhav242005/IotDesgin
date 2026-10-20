from iotdesign.live import LivePreviews


def test_preview_expires_without_any_further_uploads(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('iotdesign.live.time.monotonic', lambda: clock[0])
    previews = LivePreviews()
    previews.put('scan', 'phone', b'first')
    assert previews.get('scan').jpeg == b'first'
    clock[0] += 3.1
    assert previews.get('scan') is None


def test_preview_keeps_only_latest_per_phone_and_bounds_memory():
    previews = LivePreviews()
    previews.put('old', 'phone', b'first')
    previews.put('new', 'phone', b'second')
    assert previews.get('old') is None
    assert previews.get('new').jpeg == b'second'
    for index in range(30):
        previews.put(f'scan-{index}', f'phone-{index}', b'frame')
    assert sum(previews.get(f'scan-{index}') is not None for index in range(30)) == 16
