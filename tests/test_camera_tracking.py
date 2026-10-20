import numpy as np
from iotdesign.camera_tracking import align_to_tracking


def fixture():
    rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    offset = np.array([1., 2, 3])
    cameras, samples = [], {}
    for i in range(5):
        pose = np.eye(4); pose[0, 3] = i * 0.2
        cameras.append({'frame': str(i), 'camera_to_world': pose.tolist()})
        ar = np.eye(4)
        ar[:3, :3] = rotation @ np.diag([1, -1, -1])
        ar[:3, 3] = 2 * rotation @ pose[:3, 3] + offset
        samples[str(i)] = {'session_id': 'a' * 32, 'tracking_state': 'TRACKING',
                           'timestamp_ns': i * 100000000, 'ar_timestamp_ns': i * 100000000,
                           'camera_to_world': ar.flatten(order='F').tolist()}
    return cameras, samples, rotation, offset


def test_visual_inertial_camera_positions_and_rotations_align_geometry():
    cameras, samples, rotation, offset = fixture()
    points = np.array([[0, 0, 1], [2, 3, 4]], dtype=float)
    transformed, updated, info = align_to_tracking(points, cameras, samples)
    assert info['status'] == 'arcore_aligned'
    assert np.allclose(transformed, 2 * points @ rotation.T + offset)
    assert np.allclose(np.array(updated[0]['camera_to_world'])[:3, 3], offset)
    assert info['residual_m'] < 1e-8 and abs(info['scale'] - 2) < 1e-8


def test_stale_paused_stationary_or_conflicting_tracking_is_not_applied():
    cameras, samples, _, _ = fixture()
    points = np.array([[1., 2, 3]])
    for sample in samples.values():
        sample['ar_timestamp_ns'] += 200000000
    assert align_to_tracking(points, cameras, samples)[2]['status'] == 'unavailable'
    cameras, samples, _, _ = fixture()
    for sample in samples.values():
        sample['tracking_state'] = 'PAUSED'
    assert align_to_tracking(points, cameras, samples)[2]['status'] == 'unavailable'
    cameras, samples, _, _ = fixture()
    for sample in samples.values():
        sample['camera_to_world'][12:15] = [0., 0., 0.]
    assert align_to_tracking(points, cameras, samples)[2]['status'] == 'insufficient_motion'
    cameras, samples, _, _ = fixture()
    samples['4']['camera_to_world'][12] += 10
    result, _, info = align_to_tracking(points, cameras, samples)
    assert info['status'] == 'inconsistent_poses'
    assert np.array_equal(result, points)


def test_arcore_session_origins_are_never_fitted_together():
    cameras, samples, _, _ = fixture()
    for i in range(3):
        samples[str(i)]['session_id'] = str(i) * 32
    assert align_to_tracking(np.ones((2, 3)), cameras, samples)[2]['status'] == 'insufficient_motion'


def test_android_camera_timestamp_matches_pts_despite_different_arcore_clock():
    cameras, samples, _, _ = fixture()
    for sample in samples.values():
        sample['camera_timestamp_ns'] = sample['timestamp_ns']
        sample['ar_timestamp_ns'] += 93_855_000_000_000
    assert align_to_tracking(np.ones((2, 3)), cameras, samples)[2]['status'] == 'arcore_aligned'


def test_phone_floor_is_used_only_in_an_accepted_tracking_coordinate_system():
    cameras, samples, _, _ = fixture()
    samples['4']['floor'] = {'origin': [0., -1., 0.], 'normal': [0., 1., 0.]}
    info = align_to_tracking(np.ones((2, 3)), cameras, samples)[2]
    assert info['floor']['source'] == 'phone_selected_horizontal_plane'
    assert info['floor']['origin'] == [0., -1., 0.]
    for record in samples.values():
        record['tracking_state'] = 'PAUSED'
    assert 'floor' not in align_to_tracking(np.ones((2, 3)), cameras, samples)[2]


def test_encoder_uptime_conversion_preserves_raw_pts_for_a_real_time_camera_clock():
    cameras, samples, _, _ = fixture()
    offset = 93_854_800_000_000
    for record in samples.values():
        record['encoder_timestamp_ns'] = record['timestamp_ns']
        record['timestamp_ns'] += offset
        record['camera_timestamp_ns'] = record['timestamp_ns']
        record['timestamp_clock'] = 'camera_realtime_from_encoder_uptime'
        record['encoder_uptime_offset_ns'] = offset
    assert align_to_tracking(np.ones((2, 3)), cameras, samples)[2]['status'] == 'arcore_aligned'
    for record in samples.values():
        record['camera_timestamp_ns'] += 200_000_000
    assert align_to_tracking(np.ones((2, 3)), cameras, samples)[2]['status'] == 'unavailable'
