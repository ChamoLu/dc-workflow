"""Small SQLite repository with durable requests, events, and fenced publication."""

import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from ..domain import AgentError, Result, Task


class Repository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{(self.root / 'tasks.sqlite').as_posix()}", connect_args={"timeout": 15}
        )
        metadata = MetaData()
        self.tasks = Table(
            "tasks",
            metadata,
            Column("id", String, primary_key=True),
            Column("request_id", String, unique=True),
            Column("input_hash", String),
            Column("payload", JSON),
            Column("status", String),
            Column("result", JSON),
            Column("updated", Float),
        )
        self.events = Table(
            "events",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("task_id", String),
            Column("stage", String),
            Column("details", JSON),
            Column("created", Float),
        )
        self.leases = Table(
            "leases",
            metadata,
            Column("source_key", String, primary_key=True),
            Column("token", String),
            Column("expires", Float),
        )
        self.sources = Table(
            "sources",
            metadata,
            Column("source_key", String, primary_key=True),
            Column("artifact", JSON),
        )
        metadata.create_all(self.engine)

    def register(self, task: Task) -> dict:
        task_id = str(uuid4())
        try:
            with self.engine.begin() as db:
                db.execute(
                    insert(self.tasks).values(
                        id=task_id,
                        request_id=task.request_id,
                        input_hash=task.input_hash,
                        payload=task.model_dump(),
                        status="received",
                        result=None,
                        updated=time.time(),
                    )
                )
        except IntegrityError:
            pass
        with self.engine.connect() as db:
            row = (
                db.execute(select(self.tasks).where(self.tasks.c.request_id == task.request_id))
                .mappings()
                .one()
            )
            if row["input_hash"] != task.input_hash:
                raise AgentError(
                    "IDEMPOTENCY_CONFLICT", "request_id already exists with different input"
                )
            return dict(row)

    def set_result(self, result: Result) -> None:
        with self.engine.begin() as db:
            db.execute(
                update(self.tasks)
                .where(self.tasks.c.id == result.task_id)
                .values(status=result.status, result=result.model_dump(), updated=time.time())
            )

    def event(self, task_id: str, stage: str, details: dict | None = None) -> None:
        with self.engine.begin() as db:
            db.execute(
                insert(self.events).values(
                    task_id=task_id, stage=stage, details=details or {}, created=time.time()
                )
            )
            db.execute(
                update(self.tasks)
                .where(self.tasks.c.id == task_id)
                .values(status="running", updated=time.time())
            )

    def claim(self, key: str, ttl: float) -> str | None:
        token, now = str(uuid4()), time.time()
        try:
            with self.engine.begin() as db:
                db.execute(
                    delete(self.leases).where(
                        self.leases.c.source_key == key, self.leases.c.expires <= now
                    )
                )
                db.execute(
                    insert(self.leases).values(source_key=key, token=token, expires=now + ttl)
                )
            return token
        except IntegrityError:
            return None

    def release(self, key: str, token: str) -> None:
        with self.engine.begin() as db:
            db.execute(
                delete(self.leases).where(
                    self.leases.c.source_key == key, self.leases.c.token == token
                )
            )

    def current(self, key: str) -> dict | None:
        with self.engine.connect() as db:
            return db.execute(
                select(self.sources.c.artifact).where(self.sources.c.source_key == key)
            ).scalar_one_or_none()

    def activate(
        self,
        key: str,
        token: str,
        artifact: dict,
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> None:
        with self.engine.begin() as db:
            # A conditional write obtains SQLite's writer lock before filesystem publication.
            changed = db.execute(
                update(self.leases)
                .where(
                    self.leases.c.source_key == key,
                    self.leases.c.token == token,
                    self.leases.c.expires > time.time(),
                )
                .values(expires=time.time() + 30)
            ).rowcount
            if changed != 1:
                raise AgentError(
                    "LEASE_LOST", "Generation lease expired; candidate was not activated"
                )
            if before_commit:
                before_commit()
            db.execute(delete(self.sources).where(self.sources.c.source_key == key))
            db.execute(insert(self.sources).values(source_key=key, artifact=artifact))

    def last_stage(self, task_id: str) -> str | None:
        with self.engine.connect() as db:
            return db.execute(
                select(self.events.c.stage)
                .where(self.events.c.task_id == task_id)
                .order_by(self.events.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()

    def deactivate(self, key: str) -> None:
        with self.engine.begin() as db:
            db.execute(delete(self.sources).where(self.sources.c.source_key == key))

    def task(self, task_id: str) -> dict | None:
        with self.engine.connect() as db:
            row = (
                db.execute(select(self.tasks).where(self.tasks.c.id == task_id)).mappings().first()
            )
            return dict(row) if row else None

    def close(self) -> None:
        self.engine.dispose()
