"""Source-pixel provenance, object associations, and reversible reviewed point filtering."""
import json
import shutil
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def source_coordinates(sizes, output_height, output_width):
    """Match official VGGT crop preprocessing, batch padding, and align_corners=False resize."""
    resized = [(518, round(h * 518 / w / 14) * 14) for w, h in sizes]
    if any(h < 14 for _, h in resized):
        raise ValueError('Image aspect ratio is too extreme for VGGT preprocessing')
    batch_height = max(min(h, 518) for _, h in resized)
    coordinates = []
    for width, height in resized:
        crop_y = max(0, (height - 518) // 2)
        pad_y = (batch_height - min(height, 518)) // 2
        u = (np.arange(output_width) + 0.5) / output_width
        v = ((np.arange(output_height) + 0.5) * batch_height / output_height
             - pad_y + crop_y) / height
        x, y = np.meshgrid(u, v)
        coordinates.append(np.stack((x, y), axis=-1))
    return np.asarray(coordinates, dtype=np.float32)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def save_arrays(path, arrays):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as handle:
        np.savez_compressed(handle, **arrays)
    temporary.replace(path)


def load_arrays(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key].copy() for key in data.files}


def region_indices(data, frame_number, frame, obj, mask_arrays):
    indices = np.flatnonzero(data['frame_index'] == frame_number)
    uv = data['source_uv'][indices]
    valid = np.isfinite(uv).all(axis=1) & (uv >= 0).all(axis=1) & (uv < 1).all(axis=1)
    indices, uv = indices[valid], uv[valid]
    mask = np.unpackbits(mask_arrays[frame['mask_array']][obj['mask_index']], bitorder='little',
                         count=frame['height'] * frame['width']).reshape(frame['height'], frame['width'])
    x = np.floor(uv[:, 0] * frame['width']).astype(int)
    y = np.floor(uv[:, 1] * frame['height']).astype(int)
    return indices[mask[y, x].astype(bool)]


def associations(folder, data):
    metadata = json.loads((folder / 'objects.json').read_text(encoding='utf-8'))
    cameras = json.loads((folder / 'cameras.json').read_text(encoding='utf-8'))
    numbers = {camera['frame']: index for index, camera in enumerate(cameras)}
    if 'source_uv' not in data or 'frame_index' not in data:
        raise ValueError('This scene has no source-pixel provenance; reconstruct it with the current worker')
    mask_arrays = load_arrays(folder / 'objects.npz')
    for frame in metadata['frames']:
        if frame['frame_id'] not in numbers:
            raise ValueError('Segmentation frame does not match reconstructed camera provenance')
        for obj in sorted(frame['objects'], key=lambda obj: -obj['area_pixels']):
            yield frame, obj, region_indices(data, numbers[frame['frame_id']], frame, obj, mask_arrays)


def associate_objects(folder):
    folder = Path(folder)
    data = load_arrays(folder / 'viewer.npz')
    metadata = json.loads((folder / 'objects.json').read_text(encoding='utf-8'))
    by_id = {obj['id']: obj for f in metadata['frames'] for obj in f['objects']}
    all_ids = list(by_id)
    id_index = {key: index for index, key in enumerate(all_ids)}
    data['object_index'] = np.full(len(data['points']), -1, dtype=np.int32)
    data['semantic_colors'] = data['colors'].copy()
    for _, obj, indices in associations(folder, data):
        target = by_id[obj['id']]
        target['point_count'] = len(indices)
        unreliable = (obj.get('label') or '').lower() in {'mirror', 'window'}
        target['geometry_status'] = 'untrusted_material' if unreliable else 'unscaled_candidate'
        target['centroid_unscaled'] = (data['points'][indices].mean(axis=0).tolist()
                                       if len(indices) >= 16 and not unreliable else None)
        data['object_index'][indices] = id_index[obj['id']]
        data['semantic_colors'][indices] = obj.get('color_rgb', [120, 220, 140])
    metrics = {'regions': len(all_ids), 'assigned_points': int((data['object_index'] >= 0).sum()),
               'coordinates': 'unscaled', 'ids_are_tracks': False}
    atomic_json(folder / 'objects.json', metadata)
    save_arrays(folder / 'viewer.npz', data)
    save_arrays(folder / 'viewer-base.npz', data)
    shutil.copy2(folder / 'cloud.ply', folder / 'cloud-unfiltered.ply')
    review = {'version': 1, 'revision': 0, 'excluded_ids': [], 'point_count_before': len(data['points']),
              'point_count_after': len(data['points']), 'camera_poses_changed': False}
    atomic_json(folder / 'objects-review.json', review)
    manifest = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
    manifest['artifacts']['object_review'] = 'objects-review.json'
    manifest['artifacts']['unfiltered_point_cloud'] = 'cloud-unfiltered.ply'
    manifest['metrics']['object_geometry'] = metrics
    manifest['metrics']['review'] = review
    atomic_json(folder / 'scene.json', manifest)
    return metrics


def apply_review(folder, excluded_ids, expected_revision):
    """Filter the immutable baseline by the union of explicitly reviewed regions."""
    folder = Path(folder)
    previous = json.loads((folder / 'objects-review.json').read_text(encoding='utf-8'))
    if previous['revision'] != expected_revision:
        raise ValueError('Review changed in another window; reload before applying changes')
    data = load_arrays(folder / 'viewer-base.npz')
    metadata = json.loads((folder / 'objects.json').read_text(encoding='utf-8'))
    known = {obj['id'] for f in metadata['frames'] for obj in f['objects']}
    excluded = set(excluded_ids)
    if excluded - known:
        raise ValueError('Unknown object IDs in review')
    keep = np.ones(len(data['points']), dtype=bool)
    for _, obj, indices in associations(folder, data):
        if obj['id'] in excluded:
            keep[indices] = False
    if not keep.any():
        raise ValueError('Review would remove every point; retain some room geometry')
    filtered = {key: value[keep] for key, value in data.items()}
    vertices = np.empty(int(keep.sum()), dtype=[('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
                                               ('red', 'u1'), ('green', 'u1'), ('blue', 'u1')])
    for index, name in enumerate(('x', 'y', 'z')):
        vertices[name] = filtered['points'][:, index]
    for index, name in enumerate(('red', 'green', 'blue')):
        vertices[name] = filtered['colors'][:, index]
    temporary = folder / 'cloud.ply.tmp'
    PlyData([PlyElement.describe(vertices, 'vertex')], text=False).write(str(temporary))
    save_arrays(folder / 'viewer.npz', filtered)
    temporary.replace(folder / 'cloud.ply')
    review = {'version': 1, 'revision': previous['revision'] + 1, 'excluded_ids': sorted(excluded),
              'point_count_before': len(keep), 'point_count_after': int(keep.sum()),
              'camera_poses_changed': False}
    manifest = json.loads((folder / 'scene.json').read_text(encoding='utf-8'))
    manifest['metrics']['review'] = review
    manifest['metrics']['point_count'] = int(keep.sum())
    atomic_json(folder / 'objects-review.json', review)
    atomic_json(folder / 'scene.json', manifest)
    return review
