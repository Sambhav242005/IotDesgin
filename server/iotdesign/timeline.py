"""Read-only reconstruction history; source timing never implies sensor alignment."""
import json
import re
from urllib.parse import quote


def reconstruction_timeline(db, settings, scan_id):
    with db.connect() as connection:
        frames = [dict(row) for row in connection.execute(
            'SELECT id,timestamp,width,height FROM frames WHERE scan_id=? ORDER BY timestamp,id', (scan_id,))]
        jobs = [dict(row) for row in connection.execute(
            'SELECT id,state,created_at,updated_at,metrics FROM jobs WHERE scan_id=? ORDER BY created_at,id',
            (scan_id,))]
        inputs = list(connection.execute(
            'SELECT i.job_id,i.frame_id FROM job_inputs i JOIN jobs j ON j.id=i.job_id '
            'WHERE j.scan_id=? ORDER BY j.created_at,j.id,i.ordinal', (scan_id,)))
    by_job = {job['id']: [] for job in jobs}
    by_frame = {}
    for item in inputs:
        by_job[item['job_id']].append(item['frame_id'])
        by_frame.setdefault(item['frame_id'], []).append(item['job_id'])
    states = {job['id']: job['state'] for job in jobs}
    tracking = settings.data / 'scans' / scan_id / 'tracking'
    origins = {}
    for frame in frames:
        match = re.fullmatch(r'video-([a-f0-9]{32})-(\d+)', frame['id'])
        frame.update(video_session_id=None, video_frame=None, video_pts_seconds=None, video_time_source=None,
                     capture_elapsed_seconds=None, capture_time_source='unavailable')
        if match:
            session, number = match[1], int(match[2])
            # FFmpeg generates the retained HLS timeline at 30 fps from raw H.264.
            # This maps source picture ordinals to playback, not sensor time.
            frame.update(video_session_id=session, video_frame=number, video_pts_seconds=(number - 1) / 30,
                         video_time_source='nominal_remux_30fps')
            path = tracking / f"{frame['id']}.json"
            if path.is_file():
                sample = json.loads(path.read_text(encoding='utf-8'))
                pts = sample.get('encoder_timestamp_ns')
                source = 'encoder_pts'
                if pts is None:
                    pts = sample.get('timestamp_ns')
                    source = 'tracking_timestamp'
                if isinstance(pts, int) and number == 1:
                    origins[session] = (source, pts)
                if isinstance(pts, int) and session in origins:
                    clock_source, first_pts = origins[session]
                    if source == clock_source and pts >= first_pts:
                        frame.update(capture_elapsed_seconds=(pts - first_pts) / 1e9, capture_time_source=source)
        frame['thumbnail_url'] = f'/desktop/scans/{quote(scan_id)}/frames/{quote(frame["id"])}?thumbnail=true'
        frame['job_ids'] = by_frame.get(frame['id'], [])
        memberships = {states[job] for job in frame['job_ids']}
        frame['status'] = ('completed' if 'completed' in memberships else
                           'processing' if memberships & {'queued', 'running'} else
                           'failed' if 'failed' in memberships else 'received')
    snapshots = []
    for job in jobs:
        if job['state'] != 'completed':
            continue
        metrics = json.loads(job['metrics']) if job['metrics'] else {}
        snapshots.append({'job_id': job['id'], 'created_at': job['created_at'],
                          'completed_at': job['updated_at'], 'frame_ids': by_job[job['id']],
                          'point_count': metrics.get('point_count'), 'metrics': metrics,
                          'tracking_status': metrics.get('camera_tracking', {}).get('status', 'unavailable')})
    reports = sorted((settings.data / 'scans' / scan_id / 'colmap').glob('*/report.json'),
                     key=lambda path: path.stat().st_mtime_ns)
    comparison = json.loads(reports[-1].read_text(encoding='utf-8')) if reports else None
    return {'frames': frames, 'snapshots': snapshots, 'comparison': comparison}
