import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
from iotdesign import colmap_validation as cv
from iotdesign.config import Settings
from iotdesign.db import Database


def pose_fixture(count=6):
    cameras, samples = [], {}
    rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    for index in range(count):
        frame = f'video-{"a" * 32}-{index + 1:09}'
        pose = np.eye(4)
        pose[:3, 3] = [index * .2, index ** 2 * .01, 0]
        cameras.append({'frame': frame, 'camera_to_world': pose.tolist()})
        ar = np.eye(4)
        ar[:3, :3] = rotation @ np.diag([1, -1, -1])
        ar[:3, 3] = 2 * rotation @ pose[:3, 3] + [1, 2, 3]
        samples[frame] = {'session_id': 'a' * 32, 'video_frame': index + 1, 'tracking_state': 'TRACKING',
                          'timestamp_ns': index * 100_000_000, 'camera_timestamp_ns': index * 100_000_000,
                          'ar_timestamp_ns': index * 100_000_000,
                          'camera_to_world': ar.flatten(order='F').tolist()}
    return cameras, samples


def test_image_only_comparison_reports_similarity_and_per_frame_errors():
    cameras, samples = pose_fixture()
    result = cv.compare_poses(cameras, samples)
    assert result['status'] == 'consistent'
    assert result['registered_coverage'] == result['synchronized_registered_coverage'] == 1
    assert result['timestamp_offset_fitted'] is False
    assert result['scale_validation'] == 'estimated_not_measured'
    assert result['sessions'][0]['similarity']['scale'] == pytest.approx(2)
    assert result['sessions'][0]['position_rmse_m'] < 1e-8
    assert max(r['fit_orientation_error_deg'] for r in result['frames']) < 1e-5


def test_clock_mismatch_is_inconclusive_never_offset_fitted():
    cameras, samples = pose_fixture()
    for sample in samples.values():
        sample['camera_timestamp_ns'] += 93_855_000_000_000
    result = cv.compare_poses(cameras, samples)
    assert result['status'] == 'inconclusive' and result['matched_cameras'] == 0
    assert {r['status'] for r in result['frames']} == {'timestamp_mismatch'}
    assert result['frames'][0]['timestamp_delta_ns'] == 93_855_000_000_000
    assert result['timestamp_offset_fitted'] is False
    # Genuine upstream clock conversion is accepted as supplied, with raw clock retained.
    for sample in samples.values():
        sample['encoder_timestamp_ns'] = sample['timestamp_ns']
        sample['timestamp_ns'] = sample['camera_timestamp_ns']
        sample['timestamp_clock'] = 'camera_realtime_from_encoder_uptime'
    result = cv.compare_poses(cameras, samples)
    assert result['status'] == 'consistent'
    assert 'encoder_timestamp_ns' in result['frames'][0]


def test_timestamp_gate_boundary_and_paused_tracking():
    cameras, samples = pose_fixture()
    for sample in samples.values():
        sample['camera_timestamp_ns'] += 100_000_000
    assert cv.compare_poses(cameras, samples)['status'] == 'consistent'
    for sample in samples.values():
        sample['camera_timestamp_ns'] += 1
    assert cv.compare_poses(cameras, samples)['matched_cameras'] == 0
    for sample in samples.values():
        sample['tracking_state'] = 'PAUSED'
    assert {r['status'] for r in cv.compare_poses(cameras, samples)['frames']} == {'tracking_not_active'}


def test_separate_arcore_origins_do_not_create_a_combined_fit():
    cameras, samples = pose_fixture()
    # Non-video frames allow deliberate distinct session IDs; video IDs must join exactly.
    for index, camera in enumerate(cameras):
        old = camera['frame']
        camera['frame'] = str(index)
        samples[str(index)] = samples.pop(old)
        if index >= 3:
            samples[str(index)]['session_id'] = 'b' * 32
            samples[str(index)]['camera_to_world'][12] += 100
    result = cv.compare_poses(cameras, samples)
    assert result['status'] == 'consistent' and len(result['sessions']) == 2
    assert all(s['matched_cameras'] == 3 for s in result['sessions'])
    assert abs(result['sessions'][0]['similarity']['translation'][0]
               - result['sessions'][1]['similarity']['translation'][0]) > 99
    # Two pairs in each of three origins cannot borrow motion from other origins.
    for index, sample in enumerate(samples.values()):
        sample['session_id'] = str(index // 2) * 32
    assert cv.compare_poses(cameras, samples)['status'] == 'inconclusive'


def test_wrong_source_identity_invalid_transforms_and_stationary_phone():
    cameras, samples = pose_fixture()
    for sample in samples.values():
        sample['session_id'] = 'b' * 32
    assert {r['status'] for r in cv.compare_poses(cameras, samples)['frames']} == {'source_identity_mismatch'}
    cameras, samples = pose_fixture()
    for sample in samples.values():
        sample['camera_to_world'][0] = float('nan')
    assert {r['status'] for r in cv.compare_poses(cameras, samples)['frames']} == {'invalid_tracking_transform'}
    cameras, samples = pose_fixture()
    for camera in cameras:
        camera['camera_to_world'][0][0] = -1
    assert {r['status'] for r in cv.compare_poses(cameras, samples)['frames']} == {'invalid_image_transform'}
    cameras, samples = pose_fixture()
    for sample in samples.values():
        sample['camera_to_world'][12:15] = [1., 2., 3.]
    result = cv.compare_poses(cameras, samples)
    assert result['status'] == 'inconclusive'
    assert result['sessions'][0]['reason'] == 'insufficient_motion'


def test_inconsistent_positions_and_orientations_are_rejected():
    cameras, samples = pose_fixture()
    samples[cameras[-1]['frame']]['camera_to_world'][12] += 10
    result = cv.compare_poses(cameras, samples)
    assert result['status'] == 'rejected'
    assert any(r['fit_position_error_m'] > .1 for r in result['frames'])
    assert all('aligned_position' not in r for r in result['frames'])
    cameras, samples = pose_fixture()
    for index, sample in enumerate(samples.values()):
        angle = np.radians((-1 if index % 2 else 1) * 70)
        turn = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        pose = np.array(sample['camera_to_world']).reshape(4, 4, order='F')
        pose[:3, :3] = turn @ pose[:3, :3]
        sample['camera_to_world'] = pose.flatten(order='F').tolist()
    assert cv.compare_poses(cameras, samples)['status'] == 'rejected'


def test_low_registration_coverage_and_duplicate_mapping_cannot_pass():
    cameras, samples = pose_fixture()
    result = cv.compare_poses(cameras[:3], samples, [c['frame'] for c in cameras])
    assert result['status'] == 'inconclusive'
    assert result['reason'] == 'insufficient_registered_coverage'
    assert result['registered_coverage'] == .5
    with pytest.raises(ValueError, match='uniquely'):
        cv.compare_poses([cameras[0], cameras[0]], samples, [c['frame'] for c in cameras])


def test_mixed_orientation_outliers_cannot_hide_behind_a_good_median():
    cameras, samples = pose_fixture()
    for camera in cameras[:2]:
        sample = samples[camera['frame']]
        pose = np.array(sample['camera_to_world']).reshape(4, 4, order='F')
        pose[:3, :3] = pose[:3, :3] @ np.diag([-1., -1., 1.])
        sample['camera_to_world'] = pose.flatten(order='F').tolist()
    comparison = cv.compare_poses(cameras, samples)
    assert comparison['status'] == 'rejected'
    assert comparison['sessions'][0]['median_orientation_error_deg'] < 1e-5
    assert comparison['sessions'][0]['max_orientation_error_deg'] == pytest.approx(180)
    assert comparison['sessions'][0]['pose_inlier_coverage'] == pytest.approx(4 / 6)
    assert all(row['fit_status'] == 'rejected' for row in comparison['frames'][:2])
    assert all('aligned_position' not in row for row in comparison['frames'])


def scan_fixture(tmp_path):
    settings = Settings(data=tmp_path)
    database = Database(settings.db_path)
    folder = settings.data / 'scans' / 'scan' / 'images'
    folder.mkdir(parents=True)
    with database.connect() as connection:
        connection.execute("INSERT INTO scans VALUES ('scan','client','Room','capturing','2026')")
        for index in range(7):
            content = f'original {index}'.encode()
            path = folder / f'frame-{index}.jpg'
            path.write_bytes(content)
            connection.execute('INSERT INTO frames VALUES (?,?,?,?,?,?,?)', (
                'scan', f'frame-{index}', hashlib.sha256(content).hexdigest(), str(index), 32, 24,
                str(path.relative_to(settings.data))))
    return settings, database


def test_frozen_subset_mapping_preserves_originals_and_scan_state(tmp_path, monkeypatch):
    settings, database = scan_fixture(tmp_path)
    monkeypatch.setattr(cv, 'load_backend', lambda: SimpleNamespace(__version__='test'))

    def backend(_backend, output, inputs):
        assert [f['frame'] for f in inputs['frames']] == ['frame-0', 'frame-3', 'frame-6']
        assert [f['image_name'] for f in inputs['frames']] == [f'{i:09}.jpg' for i in range(3)]
        assert (output / 'images' / '000000001.jpg').read_bytes() == b'original 3'
        # Later capture cannot silently enter the already frozen diagnostic.
        with database.connect() as connection:
            connection.execute('INSERT INTO frames SELECT scan_id,?,sha256,?,width,height,path '
                               'FROM frames WHERE id=?', ('later', '9', 'frame-0'))
        return [], {'status': 'no_model', 'points': 0,
                    'quality': {'status': 'insufficient', 'reasons': ['no_model']}}

    monkeypatch.setattr(cv, '_reconstruct', backend)
    report = cv.run_colmap_check(settings, 'scan', 3)
    output = settings.data / 'scans' / 'scan' / 'colmap' / report['run_id']
    assert report['status'] == 'inconclusive' and report['phone_priors_used'] is False
    assert report['input_policy'] == 'sampled_diagnostic_subset' and report['available_frames'] == 7
    frozen = json.loads((output / 'inputs.json').read_text())
    assert frozen['selected_frames'] == 3 and frozen['originals_preserved'] is True
    assert json.loads((output / 'report.json').read_text()) == report
    assert not list(output.glob('*.tmp'))
    with database.connect() as connection:
        assert connection.execute('SELECT state FROM scans').fetchone()[0] == 'capturing'
        assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    assert len(list((settings.data / 'scans' / 'scan' / 'images').glob('*.jpg'))) == 7


def test_missing_backend_and_failed_run_are_explicit(tmp_path, monkeypatch):
    def missing(_name):
        raise ImportError('missing native package')
    monkeypatch.setattr(cv.importlib, 'import_module', missing)
    with pytest.raises(RuntimeError, match='Optional COLMAP backend unavailable'):
        cv.load_backend()
    settings, _ = scan_fixture(tmp_path)
    monkeypatch.setattr(cv, 'load_backend', lambda: SimpleNamespace(__version__='test'))
    (settings.data / 'scans/scan/images/frame-1.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        cv.run_colmap_check(settings, 'scan')
    reports = list((settings.data / 'scans/scan/colmap').glob('*/report.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['status'] == 'failed' and 'hash mismatch' in report['error']


@pytest.mark.parametrize('points,supported,track_length,error,reasons', [
    (3, [3] * 14, 3., .2, ['too_few_triangulated_points', 'insufficient_supported_camera_coverage']),
    (900, [90] * 6, 3., .2, []),
    (900, [90] * 6, 2., .2, ['insufficient_track_support']),
    (900, [90] * 6, 3., float('nan'), ['invalid_or_high_reprojection_error']),
])
def test_image_baseline_quality_controls_pose_conclusion(points, supported, track_length, error, reasons):
    model = SimpleNamespace(points3D=[None] * points, reg_image_ids=lambda: range(len(supported)),
                            images={i: SimpleNamespace(num_points3D=n) for i, n in enumerate(supported)},
                            compute_mean_reprojection_error=lambda: error,
                            compute_mean_track_length=lambda: track_length)
    quality = cv._image_quality(model)
    assert quality['reasons'] == reasons
    cameras, samples = pose_fixture()
    comparison = cv._gate_comparison(cv.compare_poses(cameras, samples), quality)
    assert comparison['status'] == ('inconclusive' if reasons else 'consistent')
    if reasons:
        assert comparison['reason'] == 'insufficient_image_baseline'
        assert all('aligned_position' not in row for row in comparison['frames'])
    json.dumps(comparison, allow_nan=False)
