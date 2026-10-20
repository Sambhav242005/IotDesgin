"""Optional, image-only COLMAP diagnostic and independently timed phone-pose comparison.

No phone poses or intrinsics enter reconstruction. Similarity fitting checks agreement
between estimates; it supplies neither ground truth nor a measured physical scale.
"""
import hashlib
import importlib
import json
import re
import time
import uuid
from collections import Counter

import numpy as np

from .camera_tracking import align_to_tracking
from .db import Database, now

TIMESTAMP_TOLERANCE_NS = 100_000_000
MIN_COVERAGE = 0.8
IMAGE_QUALITY_THRESHOLDS = {'minimum_points': 50, 'minimum_observations_per_camera': 20,
                            'minimum_supported_camera_coverage': .8, 'minimum_mean_track_length': 2.5,
                            'maximum_mean_reprojection_error_px': 4.0}
VIDEO_FRAME = re.compile(r'^video-([a-f0-9]{32})-(\d{9})$')


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def load_backend():
    """Keep optional native dependencies out of server startup/imports."""
    try:
        return importlib.import_module('pycolmap')
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            'Optional COLMAP backend unavailable. Install pycolmap in the server Python environment '
            '(uv pip install --python .venv/Scripts/python.exe pycolmap). '
            'Existing images and VGGT jobs are unchanged.'
        ) from exc


def _proper_pose(value, column_major=False):
    try:
        pose = np.asarray(value, dtype=float)
        if column_major:
            if pose.shape != (16,):
                return None
            pose = pose.reshape(4, 4, order='F')
        if pose.shape != (4, 4) or not np.isfinite(pose).all():
            return None
        rotation = pose[:3, :3]
        if (not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-3)
                or not np.isclose(np.linalg.det(rotation), 1, atol=1e-3)):
            return None
        return pose
    except (ValueError, TypeError):
        return None


def _tracking_pair(frame, sample):
    if not isinstance(sample, dict):
        return None, 'missing_tracking'
    if sample.get('tracking_state') != 'TRACKING':
        return None, 'tracking_not_active'
    session = sample.get('session_id')
    if not isinstance(session, str) or not re.fullmatch('[a-f0-9]{32}', session):
        return None, 'invalid_session_origin'
    source = VIDEO_FRAME.fullmatch(frame)
    if source and (source[1] != session or int(source[2]) != sample.get('video_frame')):
        return None, 'source_identity_mismatch'
    timestamp = sample.get('timestamp_ns')
    clock = sample.get('camera_timestamp_ns')
    if clock is None:
        clock = sample.get('ar_timestamp_ns')
    if any(type(value) is not int or value < 0 for value in (timestamp, clock)):
        return None, 'missing_timestamp'
    delta = abs(timestamp - clock)
    if delta > TIMESTAMP_TOLERANCE_NS:
        return None, 'timestamp_mismatch'
    pose = _proper_pose(sample.get('camera_to_world'), column_major=True)
    if pose is None:
        return None, 'invalid_tracking_transform'
    # COLMAP uses OpenCV camera axes; ARCore's physical camera uses OpenGL.
    return (pose @ np.diag([1., -1., -1., 1.]), delta), None


def compare_poses(cameras, measurements, selected_frames=None):
    """Return per-origin similarity-fit errors without ever adjusting timestamps."""
    selected_frames = list(selected_frames if selected_frames is not None else [c['frame'] for c in cameras])
    if len(set(selected_frames)) != len(selected_frames):
        raise ValueError('Duplicate source frame IDs in diagnostic inputs')
    by_frame = {}
    for camera in cameras:
        if camera['frame'] not in selected_frames or camera['frame'] in by_frame:
            raise ValueError('COLMAP cameras do not map uniquely to frozen source frames')
        by_frame[camera['frame']] = camera
    rows, groups = [], {}
    for frame in selected_frames:
        camera, sample = by_frame.get(frame), measurements.get(frame)
        record = {'frame': frame, 'registered': camera is not None}
        rows.append(record)
        if camera is None:
            record['status'] = 'unregistered'
            continue
        predicted = _proper_pose(camera.get('camera_to_world'))
        if predicted is None:
            record['status'] = 'invalid_image_transform'
            continue
        if isinstance(sample, dict):
            for key in ('session_id', 'timestamp_ns', 'camera_timestamp_ns', 'ar_timestamp_ns',
                        'encoder_timestamp_ns', 'timestamp_clock', 'sensor_timestamp_source'):
                if key in sample:
                    record[key] = sample[key]
            clock = sample.get('camera_timestamp_ns')
            if clock is None:
                clock = sample.get('ar_timestamp_ns')
            if type(sample.get('timestamp_ns')) is int and type(clock) is int:
                record['timestamp_delta_ns'] = abs(sample['timestamp_ns'] - clock)
        pair, reason = _tracking_pair(frame, sample)
        if reason:
            record['status'] = reason
            continue
        target, delta = pair
        record.update(status='matched', session_id=sample['session_id'], timestamp_delta_ns=delta)
        groups.setdefault(sample['session_id'], []).append((camera, sample, predicted, target, record))
    sessions = []
    for session_id, pairs in sorted(groups.items()):
        group_cameras = [p[0] for p in pairs]
        samples = {}
        for pair in pairs:
            sample = dict(pair[1])
            # Preserve explicit zero timestamps too. Production's helper uses
            # truthiness fallback; supplying the already selected clock in both
            # slots avoids substituting a different clock, without fitting one.
            if sample.get('camera_timestamp_ns') is not None:
                sample['ar_timestamp_ns'] = sample['camera_timestamp_ns']
            samples[pair[0]['frame']] = sample
        _, _, alignment = align_to_tracking(np.empty((0, 3)), group_cameras, samples)
        entry = {'session_id': session_id, 'matched_cameras': len(pairs), 'alignment': alignment,
                 'status': 'inconclusive', 'reason': alignment['status']}
        sessions.append(entry)
        source = np.array([p[2][:3, 3] for p in pairs])
        target = np.array([p[3][:3, 3] for p in pairs])
        span = float(np.linalg.norm(np.ptp(target, axis=0)))
        entry['phone_position_span_m'] = span
        if alignment['status'] in ('unavailable', 'insufficient_motion'):
            continue
        # Same orientation-constrained similarity as production alignment. Retain
        # residuals for rejected fits as diagnostic errors, never as usable poses.
        rotations = np.array([p[3][:3, :3] @ p[2][:3, :3].T for p in pairs])
        u, _, vt = np.linalg.svd(rotations.mean(0))
        rotation = u @ np.diag([1., 1., np.linalg.det(u @ vt)]) @ vt
        scale = alignment['scale']
        translation = target.mean(0) - scale * rotation @ source.mean(0)
        positions = scale * source @ rotation.T + translation
        errors = np.linalg.norm(positions - target, axis=1)
        angles = np.degrees(np.arccos(np.clip(
            (np.trace(rotations @ rotation.T, axis1=1, axis2=2) - 1) / 2, -1, 1)))
        position_limit = max(.05, .2 * span)
        inliers = (errors <= position_limit) & (angles <= 20)
        inlier_coverage = float(np.mean(inliers))
        consistent = alignment['status'] == 'arcore_aligned' and inlier_coverage >= MIN_COVERAGE
        entry.update(status='consistent' if consistent else 'rejected',
                     reason='similarity_fit_consistent' if consistent else 'inconsistent_poses',
                     position_rmse_m=float(np.sqrt(np.mean(errors ** 2))),
                     median_orientation_error_deg=float(np.median(angles)),
                     max_orientation_error_deg=float(np.max(angles)),
                     pose_inlier_coverage=inlier_coverage, minimum_pose_inlier_coverage=MIN_COVERAGE,
                     position_error_limit_m=position_limit, orientation_error_limit_deg=20,
                     similarity={'scale': scale, 'rotation': rotation.tolist(),
                                 'translation': translation.tolist()})
        for pair, position, error, angle, inlier in zip(pairs, positions, errors, angles, inliers):
            pair[4].update(fit_position_error_m=float(error), fit_orientation_error_deg=float(angle),
                           pose_inlier=bool(inlier),
                           fit_status='consistent' if consistent and inlier else 'rejected')
            if consistent and inlier:
                pair[4]['aligned_position'] = position.tolist()
    registered = len(by_frame)
    matched = sum(len(p) for p in groups.values())
    coverage = registered / len(selected_frames) if selected_frames else 0
    matched_coverage = matched / registered if registered else 0
    status, reason = 'inconclusive', 'no_synchronized_pose_pairs'
    if any(s['status'] == 'rejected' for s in sessions):
        status, reason = 'rejected', 'inconsistent_poses'
    elif coverage < MIN_COVERAGE:
        reason = 'insufficient_registered_coverage'
    elif sessions:
        if matched_coverage < MIN_COVERAGE:
            reason = 'insufficient_synchronized_coverage'
        elif all(s['status'] == 'consistent' for s in sessions):
            status, reason = 'consistent', 'similarity_fit_consistent'
        else:
            reason = 'insufficient_same_origin_motion'
    return {'status': status, 'reason': reason, 'selected_frames': len(selected_frames),
            'registered_frames': registered, 'registered_coverage': coverage,
            'matched_cameras': matched, 'synchronized_registered_coverage': matched_coverage,
            'timestamp_tolerance_ns': TIMESTAMP_TOLERANCE_NS, 'timestamp_offset_fitted': False,
            'minimum_coverage': MIN_COVERAGE, 'minimum_phone_span_m': 0.10,
            'minimum_same_origin_pairs': 3, 'sessions': sessions, 'frames': rows,
            'frame_status_counts': dict(Counter(row['status'] for row in rows)),
            'interpretation': 'consistency_only_not_ground_truth', 'scale_validation': 'estimated_not_measured'}


def _snapshot(settings, scan_id, output, max_frames):
    database = Database(settings.db_path)
    with database.connect() as connection:
        if not connection.execute('SELECT 1 FROM scans WHERE id=?', (scan_id,)).fetchone():
            raise ValueError('Scan not found')
        frames = [dict(row) for row in connection.execute(
            'SELECT * FROM frames WHERE scan_id=? ORDER BY timestamp,id', (scan_id,))]
    total = len(frames)
    if total < 3:
        raise ValueError('COLMAP diagnostic requires at least three saved images')
    if max_frames is not None:
        if max_frames < 3:
            raise ValueError('--max-frames must be at least 3')
        frames = [frames[i] for i in np.linspace(0, total - 1, min(total, max_frames), dtype=int)]
    images = output / 'images'
    images.mkdir(parents=True)
    records, tracking = [], {}
    for ordinal, frame in enumerate(frames):
        # Isolated names make COLMAP's image IDs/order independent of user IDs.
        name = f'{ordinal:09}.jpg'
        source = (settings.data / frame['path']).resolve()
        if not source.is_relative_to(settings.data):
            raise ValueError('Source image lies outside the data directory')
        contents = source.read_bytes()
        if hashlib.sha256(contents).hexdigest() != frame['sha256']:
            raise ValueError(f"Source image hash mismatch: {frame['id']}")
        (images / name).write_bytes(contents)
        records.append({'frame': frame['id'], 'image_name': name, 'sha256': frame['sha256'],
                        'timestamp': frame['timestamp'], 'width': frame['width'], 'height': frame['height']})
        # IDs are controlled by ingestion, but validate before constructing a path.
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', frame['id']):
            raise ValueError('Invalid source frame ID')
        path = settings.data / 'scans' / scan_id / 'tracking' / f"{frame['id']}.json"
        if path.exists():
            tracking[frame['id']] = json.loads(path.read_text(encoding='utf-8'))
    snapshot = {'frozen_at': now(), 'available_frames': total, 'selected_frames': len(records),
                'input_policy': 'sampled_diagnostic_subset' if len(records) < total else 'all_saved_frames',
                'max_frames_requested': max_frames, 'originals_preserved': True, 'frames': records}
    _write_json(output / 'inputs.json', snapshot)
    _write_json(output / 'tracking-inputs.json', tracking)
    return snapshot, tracking


def _image_quality(model):
    """Conservative diagnostic heuristics, not a calibrated accuracy guarantee."""
    supported = [model.images[index].num_points3D for index in model.reg_image_ids()]
    coverage = (sum(count >= IMAGE_QUALITY_THRESHOLDS['minimum_observations_per_camera'] for count in supported)
                / len(supported)) if supported else 0
    reprojection = float(model.compute_mean_reprojection_error())
    track_length = float(model.compute_mean_track_length())
    reasons = []
    if len(model.points3D) < IMAGE_QUALITY_THRESHOLDS['minimum_points']:
        reasons.append('too_few_triangulated_points')
    if coverage < IMAGE_QUALITY_THRESHOLDS['minimum_supported_camera_coverage']:
        reasons.append('insufficient_supported_camera_coverage')
    if not np.isfinite(track_length) or track_length < IMAGE_QUALITY_THRESHOLDS['minimum_mean_track_length']:
        reasons.append('insufficient_track_support')
    if (not np.isfinite(reprojection)
            or reprojection > IMAGE_QUALITY_THRESHOLDS['maximum_mean_reprojection_error_px']):
        reasons.append('invalid_or_high_reprojection_error')
    return {'status': 'insufficient' if reasons else 'sufficient', 'reasons': reasons,
            'threshold_basis': 'conservative_diagnostic_heuristics_not_accuracy_guarantees',
            'thresholds': IMAGE_QUALITY_THRESHOLDS,
            'supported_camera_coverage': coverage,
            'mean_track_length': track_length if np.isfinite(track_length) else None,
            'mean_reprojection_error_px': reprojection if np.isfinite(reprojection) else None}


def _gate_comparison(comparison, quality):
    comparison['image_quality'] = quality
    if quality['status'] != 'sufficient':
        comparison['pose_fit_status_before_image_quality_gate'] = comparison['status']
        comparison.update(status='inconclusive', reason='insufficient_image_baseline')
        for row in comparison['frames']:
            row.pop('aligned_position', None)
            if 'fit_status' in row:
                row['fit_status'] = 'inconclusive'
        for session in comparison['sessions']:
            session['pose_fit_status_before_image_quality_gate'] = session['status']
            session.update(status='inconclusive', reason='insufficient_image_baseline')
    return comparison


def _reconstruct(backend, output, inputs):
    database_path, images, sparse = output / 'database.db', output / 'images', output / 'sparse'
    sparse.mkdir()
    # CPU-only optional baseline never contends with the single GPU worker. No
    # priors are written to its fresh database; alignment happens after mapping.
    backend.extract_features(database_path, images, camera_mode=backend.CameraMode.AUTO,
                             extraction_options={'num_threads': 2, 'max_image_size': 1600},
                             device=backend.Device.cpu)
    backend.match_exhaustive(database_path, matching_options={'num_threads': 2}, device=backend.Device.cpu)
    maps = backend.incremental_mapping(database_path, images, sparse,
                                       options={'num_threads': 2, 'random_seed': 0, 'min_model_size': 3,
                                                'use_prior_position': False, 'ba_use_gpu': False})
    summaries = [{'model_id': int(key), 'registered_frames': len(model.reg_image_ids()),
                  'points': len(model.points3D), 'quality': _image_quality(model)}
                 for key, model in sorted(maps.items())]
    if not maps:
        return [], {'status': 'no_model', 'models': summaries, 'points': 0,
                    'quality': {'status': 'insufficient', 'reasons': ['no_model']}}
    # Different disconnected models retain separate arbitrary origins; select
    # one deterministically and report all coverages rather than merging them.
    key = min(maps, key=lambda k: (-len(maps[k].reg_image_ids()), -len(maps[k].points3D), k))
    model = maps[key]
    mapping = {frame['image_name']: frame for frame in inputs['frames']}
    cameras = []
    for image_id in model.reg_image_ids():
        image = model.images[image_id]
        if image.name not in mapping:
            raise ValueError('COLMAP returned an image outside frozen inputs')
        transform = np.eye(4)
        transform[:3] = image.cam_from_world().inverse().matrix()
        if _proper_pose(transform) is None:
            raise ValueError('COLMAP returned an invalid camera transform')
        calibration = model.cameras[image.camera_id]
        cameras.append({'frame': mapping[image.name]['frame'], 'image_name': image.name,
                        'colmap_image_id': int(image_id), 'camera_to_world': transform.tolist(),
                        'intrinsics': calibration.calibration_matrix().tolist(),
                        'camera_model': calibration.model.name, 'camera_params': calibration.params.tolist(),
                        'width': calibration.width, 'height': calibration.height})
    cameras.sort(key=lambda c: c['image_name'])
    model.export_PLY(output / 'cloud.ply')
    _write_json(output / 'cameras.json', cameras)
    return cameras, {'status': 'completed', 'models': summaries, 'selected_model_id': int(key),
                     'points': len(model.points3D), 'quality': _image_quality(model),
                     'mean_reprojection_error_px': _image_quality(model)['mean_reprojection_error_px']}


def run_colmap_check(settings, scan_id, max_frames=None):
    """Freeze saved inputs into a new local diagnostic; do not mutate scan/jobs."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', scan_id):
        raise ValueError('Invalid scan ID')
    backend = load_backend()
    output = settings.data / 'scans' / scan_id / 'colmap' / uuid.uuid4().hex
    output.mkdir(parents=True)
    started = time.monotonic()
    report = {'version': 1, 'scan_id': scan_id, 'run_id': output.name, 'created_at': now(),
              'status': 'running', 'backend': {'name': 'pycolmap', 'version': backend.__version__, 'device': 'cpu'},
              'representation': 'sparse_point_cloud', 'coordinates': 'unscaled',
              'camera_convention': 'opencv_camera_to_world', 'phone_priors_used': False,
              'interpretation': 'consistency_only_not_ground_truth', 'scale_validation': 'estimated_not_measured',
              'artifacts': {'inputs': 'inputs.json', 'tracking_inputs': 'tracking-inputs.json'}}
    _write_json(output / 'report.json', report)
    try:
        inputs, tracking = _snapshot(settings, scan_id, output, max_frames)
        report.update(input_policy=inputs['input_policy'], available_frames=inputs['available_frames'],
                      selected_frames=inputs['selected_frames'], originals_preserved=True)
        cameras, reconstruction = _reconstruct(backend, output, inputs)
        report['reconstruction'] = reconstruction
        report['comparison'] = compare_poses(cameras, tracking, [f['frame'] for f in inputs['frames']])
        _gate_comparison(report['comparison'], reconstruction['quality'])
        report['status'] = report['comparison']['status']
        if reconstruction['status'] == 'completed':
            report['artifacts'].update(cameras='cameras.json', point_cloud='cloud.ply')
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        report['duration_seconds'] = time.monotonic() - started
        report['finished_at'] = now()
        _write_json(output / 'report.json', report)
    return report
