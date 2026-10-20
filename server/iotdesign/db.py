import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, name TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'capturing', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS frames (
                    scan_id TEXT NOT NULL, id TEXT NOT NULL, sha256 TEXT NOT NULL,
                    timestamp TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                    path TEXT NOT NULL, PRIMARY KEY(scan_id,id),
                    FOREIGN KEY(scan_id) REFERENCES scans(id)
                );
                CREATE INDEX IF NOT EXISTS ordered_scan_frames ON frames(scan_id,timestamp,id);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, state TEXT NOT NULL,
                    stage TEXT NOT NULL, frame_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    error TEXT, metrics TEXT, artifact_dir TEXT,
                    FOREIGN KEY(scan_id) REFERENCES scans(id)
                );
                CREATE TABLE IF NOT EXISTS worker (
                    id INTEGER PRIMARY KEY CHECK(id=1), heartbeat TEXT NOT NULL,
                    worker_id TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_job_per_scan ON jobs(scan_id)
                    WHERE state IN ('queued','running');
                CREATE TABLE IF NOT EXISTS stream_sessions (
                    scan_id TEXT PRIMARY KEY REFERENCES scans(id),
                    last_queued_count INTEGER NOT NULL DEFAULT 0,
                    stopped INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS job_inputs (
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    frame_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    PRIMARY KEY(job_id, frame_id)
                );
            """)

    @contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get_meta(self, key, factory):
        with self.connect(immediate=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if row:
                return row[0]
            value = factory()
            db.execute("INSERT INTO meta VALUES (?,?)", (key, value))
            return value

    def worker_ready(self, stale_seconds):
        with self.connect() as db:
            row = db.execute("SELECT heartbeat FROM worker WHERE id=1").fetchone()
        return bool(row and (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
                    < stale_seconds)

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metrics"] = json.loads(result["metrics"]) if result["metrics"] else None
        result.pop("artifact_dir")
        return result
