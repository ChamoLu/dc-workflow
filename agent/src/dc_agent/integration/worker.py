"""One isolated spawn worker; a supervisor fences and kills overdue process trees."""

import logging
import multiprocessing
import os
import signal
import subprocess
import threading
import time
from uuid import uuid4

from ..domain import Task
from ..infrastructure.locking import publication_lock
from .jobs import JobStore

logger = logging.getLogger(__name__)


def run_once(
    settings, store: JobStore, *, owner="test-worker", service_factory=None, stop_after_job=None
) -> bool:
    row = store.claim(owner, settings.api_generation_timeout)
    if row is None:
        return False
    token, job_id = row["worker_token"], row["job_id"]
    service = None
    try:
        if service_factory is None:
            from ..bootstrap import build_service

            service_factory = build_service
        service = service_factory(settings)
        remaining = row["generation_deadline"] - time.time()
        if remaining <= 0:
            store.fail(job_id, "BUDGET_EXCEEDED")
            return True
        task = Task.model_validate({**row["payload"], "request_id": f"http-v3-{job_id}"})
        result = service.run(task, deadline=time.monotonic() + remaining)
        result = result.model_dump() if hasattr(result, "model_dump") else result
        if result.get("status") == "processing":
            store.defer(job_id, token)
        elif not store.finish(job_id, token, result):
            # The API or watchdog may already have expired/fenced this lease.
            store.fail(job_id, "BUDGET_EXCEEDED")
    except Exception:
        # Do not persist raw response bodies, settings, credentials or exception messages.
        store.finish(
            job_id,
            token,
            {
                "status": "failed",
                "error": {"code": "WORKER_ERROR"},
            },
        )
        logger.error(
            "Integration worker failed for job %s; inspect local framework diagnostics", job_id
        )
    finally:
        if service is not None:
            try:
                service.close()
            except Exception:
                logger.error("Integration worker resource cleanup failed for job %s", job_id)
        if stop_after_job is not None and time.time() >= row["deadline_at"]:
            # A watchdog may already have observed this attempt's expired token.
            # Never claim another job in that same process before it is reaped.
            stop_after_job.set()
    return True


def _worker_main(settings, owner, stop_event):
    if os.name != "nt":
        # Only this child enters its own session; the API's process group is untouched.
        os.setsid()
    store = JobStore(settings.resolved_runtime())
    stop_after_job = threading.Event()
    while not stop_event.is_set():
        if not run_once(settings, store, owner=owner, stop_after_job=stop_after_job):
            stop_event.wait(settings.api_worker_poll_interval)
        if stop_after_job.is_set():
            return


def terminate_worker_tree(process):
    """Stop the owned worker; Windows taskkill also stops local runner descendants.

    POSIX process groups and remote Docker containers need a separate deployment
    audit before using this local Windows integration as a server installation.
    """
    if not process.is_alive():
        process.join(timeout=1)
        return
    if os.name == "nt":
        # taskkill /T is essential: multiprocessing.terminate alone orphans runner children.
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode and process.is_alive():
                logger.error("Owned worker process tree termination returned a failure")
        except (OSError, subprocess.TimeoutExpired):
            logger.error("Owned worker process tree termination was unavailable")
    else:
        try:
            # setsid in _worker_main guarantees pgid==pid before it spawns candidates.
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.terminate()  # Child has not reached setsid yet; no descendants exist.
        except ProcessLookupError:
            pass
    process.join(timeout=2)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


class WorkerSupervisor:
    def __init__(self, settings, store: JobStore):
        self.settings, self.store = settings, store
        self.owner = str(uuid4())
        self.context = multiprocessing.get_context("spawn")
        self.stop_event = self.context.Event()
        self.monitor_stop = threading.Event()
        self.process = None
        self.thread = None
        self.lock = None
        self.restarts = 0

    def _spawn(self):
        self.process = self.context.Process(
            target=_worker_main,
            args=(self.settings, self.owner, self.stop_event),
            name="dc-agent-v3-worker",
        )
        # A non-daemon worker can start parser subprocesses and is stopped on lifespan exit.
        self.process.start()

    def start(self):
        self.lock = publication_lock(
            self.settings.resolved_runtime() / ".integration-service.lock", timeout=0.2
        )
        self.lock.__enter__()
        try:
            self.store.recover(code="SERVICE_RESTARTED")
            self._spawn()
            self.thread = threading.Thread(
                target=self._monitor, name="dc-agent-worker-watchdog", daemon=True
            )
            self.thread.start()
        except BaseException:
            self.lock.__exit__(None, None, None)
            self.lock = None
            raise

    def _monitor(self):
        while not self.monitor_stop.wait(self.settings.api_worker_poll_interval):
            if self.process is None:
                return
            overdue = [
                row for row in self.store.active(self.owner) if row["deadline_at"] <= time.time()
            ]
            if overdue:
                terminate_worker_tree(self.process)
                for row in overdue:
                    self.store.fail(row["job_id"], "BUDGET_EXCEEDED")
                self.store.recover(owner=self.owner, code="BUDGET_EXCEEDED")
                if not self.monitor_stop.is_set():
                    self._spawn()
            elif not self.process.is_alive():
                self.process.join(timeout=1)
                self.store.recover(owner=self.owner)
                self.restarts += 1
                if self.restarts > 3:
                    logger.error("Integration worker restart limit exceeded")
                    return
                if not self.monitor_stop.is_set():
                    self._spawn()

    def is_alive(self):
        return bool(self.process and self.process.is_alive())

    def stop(self):
        self.monitor_stop.set()
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
        if self.process:
            self.process.join(timeout=2)
            terminate_worker_tree(self.process)
        self.store.recover(owner=self.owner, code="SERVICE_STOPPED")
        if self.lock:
            self.lock.__exit__(None, None, None)
            self.lock = None
