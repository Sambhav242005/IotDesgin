"""Align image-estimated cameras to synchronized ARCore poses; reject inconsistent fits."""
import numpy as np


def align_to_tracking(points, cameras, measurements):
    pairs = []
    for index, camera in enumerate(cameras):
        sample = measurements.get(camera['frame'])
        if not sample or sample['tracking_state'] != 'TRACKING':
            continue
        clock = sample.get('camera_timestamp_ns') or sample.get('ar_timestamp_ns') or 0
        if abs(sample['timestamp_ns'] - clock) > 100_000_000:
            continue
        target = np.asarray(sample['camera_to_world'], dtype=float).reshape(4, 4, order='F')
        predicted = np.asarray(camera['camera_to_world'], dtype=float)
        if not np.isfinite(target).all() or not np.allclose(target[3], [0, 0, 0, 1], atol=1e-5):
            continue
        if not np.allclose(target[:3, :3].T @ target[:3, :3], np.eye(3), atol=1e-3):
            continue
        # ARCore physical camera is OpenGL; VGGT cameras are OpenCV.
        target = target @ np.diag([1., -1., -1., 1.])
        pairs.append((index, sample['session_id'], predicted, target))
    if not pairs:
        return points, cameras, {'status': 'unavailable', 'matched_cameras': 0}
    # Each resumed ARCore session has a separate origin. Never fit across origins.
    group = max({p[1] for p in pairs}, key=lambda sid: sum(p[1] == sid for p in pairs))
    pairs = [p for p in pairs if p[1] == group]
    info = {'status': 'insufficient_motion', 'matched_cameras': len(pairs), 'session_id': group}
    if len(pairs) < 3:
        return points, cameras, info
    source = np.array([p[2][:3, 3] for p in pairs])
    target = np.array([p[3][:3, 3] for p in pairs])
    x, y = source - source.mean(0), target - target.mean(0)
    if np.linalg.norm(np.ptp(target, axis=0)) < 0.10 or (x * x).sum() < 1e-8:
        return points, cameras, info
    # Camera rotations constrain orientation even for a nearly straight camera path.
    rotations = np.array([p[3][:3, :3] @ p[2][:3, :3].T for p in pairs])
    u, _, vt = np.linalg.svd(rotations.mean(0))
    rotation = u @ np.diag([1., 1., np.linalg.det(u @ vt)]) @ vt
    rotated = x @ rotation.T
    scale = float((rotated * y).sum() / (x * x).sum())
    translation = target.mean(0) - scale * rotation @ source.mean(0)
    residual = float(np.sqrt(np.mean(np.sum((scale * rotated - y)**2, axis=1))))
    angle = np.degrees(np.arccos(np.clip((np.trace(rotations @ rotation.T, axis1=1, axis2=2) - 1)/2, -1, 1)))
    info.update(scale=scale, residual_m=residual, median_orientation_error_deg=float(np.median(angle)))
    if scale <= 0 or residual > max(0.05, 0.2 * np.linalg.norm(np.ptp(target, axis=0))) or np.median(angle) > 20:
        info['status'] = 'inconsistent_poses'
        return points, cameras, info
    info['status'] = 'arcore_aligned'
    info['scale_validation'] = 'estimated_not_measured'
    floors = [measurements[cameras[p[0]]['frame']].get('floor') for p in pairs]
    for floor in reversed(floors):
        if floor:
            normal = np.asarray(floor['normal'], dtype=float)
            if np.isfinite(normal).all() and np.linalg.norm(normal) > 0.9 and normal[1] > 0.9:
                info['floor'] = {'origin': floor['origin'], 'normal': (normal / np.linalg.norm(normal)).tolist(),
                                 'source': 'phone_selected_horizontal_plane'}
                break
    updated = []
    for camera in cameras:
        transform = np.array(camera['camera_to_world'], dtype=float)
        transform[:3, :3] = rotation @ transform[:3, :3]
        transform[:3, 3] = scale * rotation @ transform[:3, 3] + translation
        updated.append({**camera, 'camera_to_world': transform.tolist()})
    return scale * np.asarray(points) @ rotation.T + translation, updated, info
