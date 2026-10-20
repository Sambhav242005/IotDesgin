"""Accumulate accepted ARCore-aligned geometry without a fixed point-count cap."""
import json
import shutil

import numpy as np
from PIL import Image

from .reconstruction import export_scene


def accumulate(db, settings, job, output, metrics):
    tracking = metrics.get('camera_tracking', {})
    if tracking.get('status') != 'arcore_aligned':
        return metrics
    session = tracking['session_id']
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT artifact_dir,metrics FROM jobs WHERE scan_id=? AND state='completed' "
            'ORDER BY updated_at DESC', (job['scan_id'],)).fetchall()
    previous = None
    for row in rows:
        old_metrics = json.loads(row['metrics'] or '{}')
        if old_metrics.get('live_map', {}).get('session_id') == session:
            previous = settings.data / row['artifact_dir']
            break
    # Preserve the independent model snapshot as well as the accumulated map.
    for name in ('viewer.npz', 'cameras.json', 'cloud.ply'):
        shutil.copyfile(output / name, output / f'snapshot-{name}')
    with np.load(output / 'viewer.npz', allow_pickle=False) as data:
        current = {key: data[key] for key in data.files}
    cameras = json.loads((output / 'cameras.json').read_text(encoding='utf-8'))
    batches = 1
    if previous:
        with np.load(previous / 'viewer.npz', allow_pickle=False) as data:
            old = {key: data[key] for key in data.files}
        old_cameras = json.loads((previous / 'cameras.json').read_text(encoding='utf-8'))
        if 'frame_index' in current:
            current['frame_index'] = current['frame_index'] + len(old_cameras)
        current = {key: np.concatenate((old[key], value)) for key, value in current.items()}
        cameras = old_cameras + cameras
        prior = json.loads((previous / 'scene.json').read_text(encoding='utf-8'))
        batches += prior['metrics']['live_map']['batches']
    # Spatial deduplication bounds repeated observations, not the size of the map.
    # New observed space may add any number of cells; there is no total-count cap.
    cells = np.floor(current['points'].astype(np.float64) / 0.01).astype(np.int64)
    _, selected = np.unique(cells, axis=0, return_index=True)
    current = {key: value[selected] for key, value in current.items()}
    metrics['live_map'] = {'session_id': session, 'batches': batches, 'voxel_size_arcore_units': 0.01,
                           'point_count_cap': None, 'mode': 'accumulated_tracked_batches'}
    provenance = {key: value for key, value in current.items() if key not in {'points', 'colors'}}
    return export_scene(output, job['scan_id'], job['id'], current['points'], current['colors'],
                        cameras, metrics, np.asarray(Image.open(output / 'preview.jpg')),
                        provenance=provenance or None)
