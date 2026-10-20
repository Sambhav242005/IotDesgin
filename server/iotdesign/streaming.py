"""Durable consecutive reconstruction batches; never skip stream inputs."""
import uuid

from .db import now

STREAM_FRAME_LIMIT = 3600  # Legacy still-upload route only, not the video decoder.


def schedule_snapshot(connection, scan_id, force=False):
    """Queue one bounded overlapping batch, retaining every unprocessed frame."""
    session = connection.execute(
        'SELECT * FROM stream_sessions WHERE scan_id=?', (scan_id,)).fetchone()
    if not session:
        return None
    active = connection.execute(
        "SELECT id FROM jobs WHERE scan_id=? AND state IN ('queued','running')", (scan_id,)).fetchone()
    if active:
        return active[0]
    latest = connection.execute(
        'SELECT id,state FROM jobs WHERE scan_id=? ORDER BY created_at DESC LIMIT 1', (scan_id,)).fetchone()
    if latest and latest['state'] == 'failed':
        # A failed batch blocks progress. Retry precisely its frozen inputs.
        if not force:
            return None
        selected = [r[0] for r in connection.execute(
            'SELECT frame_id FROM job_inputs WHERE job_id=? ORDER BY ordinal', (latest['id'],))]
        covered = session['last_queued_count']
    else:
        count = connection.execute('SELECT COUNT(*) FROM frames WHERE scan_id=?', (scan_id,)).fetchone()[0]
        previous = session['last_queued_count']
        new_count = count - previous
        if count < 8 or new_count <= 0 or (not session['stopped'] and new_count < 4 and previous):
            if session['stopped'] and count >= 8 and new_count == 0 and latest and latest['state'] == 'completed':
                connection.execute("UPDATE scans SET state='completed' WHERE id=?", (scan_id,))
            return None
        # At most 8 views fit all supported worker profiles. Four older views
        # overlap for registration; all remaining inputs wait for later batches.
        covered = min(count, previous + 4) if previous else min(count, 8)
        start = max(0, covered - 8)
        selected = [r[0] for r in connection.execute(
            'SELECT id FROM frames WHERE scan_id=? ORDER BY timestamp,id LIMIT ? OFFSET ?',
            (scan_id, covered - start, start))]
    job_id = str(uuid.uuid4())
    timestamp = now()
    connection.execute(
        "INSERT INTO jobs(id,scan_id,state,stage,frame_count,created_at,updated_at) "
        "VALUES (?,?,'queued','waiting_for_worker',?,?,?)",
        (job_id, scan_id, len(selected), timestamp, timestamp))
    connection.executemany('INSERT INTO job_inputs VALUES (?,?,?)',
                           [(job_id, frame_id, i) for i, frame_id in enumerate(selected)])
    connection.execute('UPDATE stream_sessions SET last_queued_count=? WHERE scan_id=?', (covered, scan_id))
    return job_id
