import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import Settings
from .db import Database, now
from .reconstruction import check_gpu, reconstruct

log = logging.getLogger(__name__)


@contextmanager
def worker_lock(data):
    # OS lock, released automatically even after process termination.
    import msvcrt
    with (data / 'worker.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('A GPU worker or benchmark already owns this data directory') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def recover(db):
    with db.connect(immediate=True) as connection:
        connection.execute("UPDATE jobs SET state='failed',stage='interrupted',error=?,updated_at=? "
                           "WHERE state='running'",
                           ('Worker stopped before completion. Source images preserved; retry this scan.', now()))


def claim(db):
    with db.connect(immediate=True) as connection:
        row = connection.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        connection.execute("UPDATE jobs SET state='running',stage='starting',updated_at=? WHERE id=?",
                           (now(), row['id']))
        return dict(row)


def process_job(db, settings, job, backend, segment_objects=False, semantic_objects=False,
                max_frames=20, sam_model='facebook/sam2.1-hiera-small', segmentation_edge=1024, **kwargs):
    def stage(value):
        with db.connect() as connection:
            connection.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=?', (value, now(), job['id']))
    with db.connect() as connection:
        paths = [settings.data / r[0] for r in connection.execute(
            'SELECT path FROM frames WHERE scan_id=? ORDER BY timestamp,id', (job['scan_id'],))]
    output = settings.data / 'scans' / job['scan_id'] / 'artifacts' / job['id']
    try:
        metrics = backend(paths, output, job['scan_id'], job['id'], count=min(job['frame_count'], max_frames),
                          stage=stage, **kwargs)
        metrics['requested_frames'] = job['frame_count']
        if segment_objects:
            from .reconstruction import evenly_spaced
            from .segmentation import attach_segmentation, segment_frames
            segmentation = segment_frames(evenly_spaced(paths, metrics['input_frames']), output, stage=stage,
                                          semantic=semantic_objects,
                                          model_id=sam_model, max_edge=segmentation_edge,
                                          detector_path=settings.data / 'models' / 'yolov8n-oiv7.pt')
            attach_segmentation(output, segmentation)
            metrics['segmentation'] = segmentation
            from .object_geometry import associate_objects
            metrics['object_geometry'] = associate_objects(output)
        if not (output / 'scene.json').exists():
            raise RuntimeError('Reconstruction did not produce a completed manifest')
        with db.connect(immediate=True) as connection:
            connection.execute("UPDATE jobs SET state='completed',stage='done',metrics=?,artifact_dir=?,"
                               "updated_at=?,error=NULL WHERE id=?",
                               (json.dumps(metrics), str(output.relative_to(settings.data)), now(), job['id']))
            connection.execute("UPDATE scans SET state='completed' WHERE id=?", (job['scan_id'],))
    except Exception as exc:
        log.exception('Reconstruction failed')
        with db.connect() as connection:
            connection.execute("UPDATE jobs SET state='failed',stage='failed',error=?,updated_at=? WHERE id=?",
                               (str(exc)[:2000], now(), job['id']))


def run_worker(settings: Settings, repo: Path, resolution=336, once=False, segment_objects=False,
               semantic_objects=False, max_frames=20, weight_precision='fp32', max_points=500000,
               sam_model='facebook/sam2.1-hiera-small', segmentation_edge=1024, vram_budget_gb=0):
    check_gpu(repo)
    if vram_budget_gb:
        import torch
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        if not 0 < vram_budget_gb < total:
            raise ValueError('VRAM budget must be positive and smaller than physical GPU memory')
        torch.cuda.set_per_process_memory_fraction(vram_budget_gb / total)
    db = Database(settings.db_path)
    with worker_lock(settings.data):
        recover(db)
        worker_id, stop = str(uuid.uuid4()), threading.Event()
        def heartbeat():
            while not stop.is_set():
                with db.connect() as connection:
                    connection.execute('INSERT OR REPLACE INTO worker VALUES (1,?,?)', (now(), worker_id))
                stop.wait(5)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            log.info('Windows GPU worker ready')
            while True:
                job = claim(db)
                if job:
                    process_job(db, settings, job, reconstruct, repo=repo, resolution=resolution,
                                segment_objects=segment_objects, semantic_objects=semantic_objects,
                                max_frames=max_frames, weight_precision=weight_precision,
                                max_points=max_points, sam_model=sam_model,
                                segmentation_edge=segmentation_edge)
                if once:
                    return
                if not job:
                    time.sleep(1)
        finally:
            stop.set()
            thread.join()
            with db.connect() as connection:
                connection.execute('DELETE FROM worker WHERE worker_id=?', (worker_id,))
