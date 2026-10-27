"""SQLite queue with transactional idempotency and fenced worker completion."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..domain import AgentError, digest, dumps
from .contracts import TERMINAL


class JobStore:
    def __init__(self, runtime: Path):
        runtime.mkdir(parents=True, exist_ok=True)
        self.path = runtime / "integration_jobs.sqlite"
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, identity_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                created_at REAL NOT NULL, deadline_at REAL NOT NULL,
                idempotent_until REAL NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL, result TEXT, error_code TEXT NOT NULL DEFAULT '',
                worker_token TEXT, worker_owner TEXT, generation_deadline REAL,
                available_at REAL NOT NULL, finished_at REAL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_identity ON jobs(identity_key, created_at)")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        for name in ("payload", "result"):
            if result[name]:
                result[name] = json.loads(result[name])
        return result

    @staticmethod
    def _expire(db, now):
        db.execute(
            """UPDATE jobs SET status='failed', stage='expired',
            error_code='BUDGET_EXCEEDED', finished_at=?
            WHERE status IN ('queued','processing') AND deadline_at<=?""",
            (now, now),
        )

    def submit(self, payload: dict, *, run_id: str | None, ttl: float, timeout: float) -> dict:
        fingerprint = digest(payload)
        identity = "run:" + run_id if run_id else "input:" + fingerprint
        now = time.time()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            old = db.execute(
                """SELECT * FROM jobs WHERE identity_key=?
                AND (? OR idempotent_until>? OR status IN ('queued','processing'))
                ORDER BY created_at DESC LIMIT 1""",
                (identity, bool(run_id), now),
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise AgentError(
                        "IDEMPOTENCY_CONFLICT", "Run identity already has different input"
                    )
                return self._row(old)
            job_id = str(uuid4())
            db.execute(
                """INSERT INTO jobs
                (job_id,identity_key,fingerprint,payload,created_at,deadline_at,
                 idempotent_until,status,stage,available_at)
                VALUES(?,?,?,?,?,?,?,'queued','queued',?)""",
                (
                    job_id,
                    identity,
                    fingerprint,
                    dumps(payload),
                    now,
                    now + timeout,
                    now + max(ttl, timeout),
                    now,
                ),
            )
            return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def get(self, job_id: str) -> dict | None:
        with self._connection() as db:
            self._expire(db, time.time())
            return self._row(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def claim(self, owner: str, generation_timeout: float, *, reserve: float = 15) -> dict | None:
        now = time.time()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            db.execute(
                """UPDATE jobs SET status='failed',stage='expired',
                error_code='BUDGET_EXCEEDED',finished_at=? WHERE worker_token IS NULL
                AND status IN ('queued','processing') AND
                (deadline_at<=? OR generation_deadline<=?)""",
                (now, now + reserve, now),
            )
            row = db.execute(
                """SELECT * FROM jobs WHERE status IN ('queued','processing')
                AND worker_token IS NULL AND available_at<=? ORDER BY created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            token = str(uuid4())
            deadline = row["generation_deadline"] or min(
                row["deadline_at"] - reserve, row["created_at"] + generation_timeout
            )
            if deadline <= now:
                db.execute(
                    """UPDATE jobs SET status='failed',stage='expired',
                    error_code='BUDGET_EXCEEDED',finished_at=? WHERE job_id=?""",
                    (now, row["job_id"]),
                )
                return None
            db.execute(
                """UPDATE jobs SET status='processing',stage='running',
                worker_token=?,worker_owner=?,generation_deadline=? WHERE job_id=?""",
                (token, owner, deadline, row["job_id"]),
            )
            return self._row(
                db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            )

    def finish(self, job_id: str, token: str, result: dict) -> bool:
        status = result["status"]
        if status not in TERMINAL:
            raise ValueError("Only a terminal result can finish a job")
        now = time.time()
        with self._connection() as db:
            self._expire(db, now)
            changed = db.execute(
                """UPDATE jobs SET status=?,stage=?,result=?,
                error_code=?,finished_at=?,worker_token=NULL WHERE job_id=?
                AND worker_token=? AND status='processing' AND deadline_at>?""",
                (
                    status,
                    "complete" if status == "ready" else "failed",
                    dumps(result),
                    (result.get("error") or {}).get("code", ""),
                    now,
                    job_id,
                    token,
                    now,
                ),
            )
            return changed.rowcount == 1

    def defer(self, job_id: str, token: str, delay: float = 0.5):
        with self._connection() as db:
            self._expire(db, time.time())
            db.execute(
                """UPDATE jobs SET stage='waiting_for_asset',worker_token=NULL,
                available_at=? WHERE job_id=? AND worker_token=? AND status='processing'""",
                (time.time() + delay, job_id, token),
            )

    def fail(self, job_id: str, code: str):
        with self._connection() as db:
            db.execute(
                """UPDATE jobs SET status='failed',stage='failed',error_code=?,
                worker_token=NULL,finished_at=? WHERE job_id=? AND status IN ('queued','processing')""",
                (code, time.time(), job_id),
            )

    def invalidate(self, job_id: str, code: str):
        with self._connection() as db:
            db.execute(
                """UPDATE jobs SET status='failed',stage='delivery',error_code=?
                WHERE job_id=? AND status='ready'""",
                (code, job_id),
            )

    def recover(self, *, owner: str | None = None, code: str = "WORKER_INTERRUPTED"):
        # Called only while holding the single API/worker lifetime lock.
        with self._connection() as db:
            self._expire(db, time.time())
            query = """UPDATE jobs SET status='failed',stage='interrupted',
                error_code=CASE WHEN error_code='' THEN ? ELSE error_code END,
                worker_token=NULL,finished_at=COALESCE(finished_at,?)
                WHERE worker_token IS NOT NULL AND status IN ('processing','failed')"""
            args = [code, time.time()]
            if owner:
                query += " AND worker_owner=?"
                args.append(owner)
            db.execute(query, args)

    def active(self, owner: str) -> list[dict]:
        with self._connection() as db:
            return [
                self._row(row)
                for row in db.execute(
                    """SELECT * FROM jobs
                WHERE worker_token IS NOT NULL AND worker_owner=?""",
                    (owner,),
                )
            ]
