"""Single-threaded background job queue for dashboard-triggered phases."""
from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass
class JobRecord:
    id: str
    name: str
    state: str
    run_id: str | None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: dict | None = None
    exit_code: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self, *, max_history: int = 50):
        self._jobs: dict[str, JobRecord] = {}
        self._history: deque[str] = deque(maxlen=max_history)
        self._queue: queue.Queue[tuple[str, Callable[[], tuple[dict, int]]]] = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="dashboard-jobs", daemon=True)
        self._worker.start()

    def enqueue(
        self,
        name: str,
        func: Callable[[], tuple[dict, int]],
        *,
        run_id: str | None = None,
    ) -> JobRecord:
        job_id = uuid.uuid4().hex[:12]
        record = JobRecord(
            id=job_id,
            name=name,
            state="queued",
            run_id=run_id,
            created_at=time.time(),
        )
        with self._lock:
            self._jobs[job_id] = record
            self._history.appendleft(job_id)
        self._queue.put((job_id, func))
        return record

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [
                self._jobs[job_id].to_dict()
                for job_id in self._history
                if job_id in self._jobs
            ]

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            record = self._jobs.get(job_id)
            return None if record is None else record.to_dict()

    def _run(self) -> None:
        while True:
            job_id, func = self._queue.get()
            try:
                self._mark_started(job_id)
                result, exit_code = func()
                self._mark_finished(job_id, result, exit_code)
            except Exception as exc:  # noqa: BLE001 - preserve failures for UI polling
                self._mark_failed(job_id, exc)
            finally:
                self._queue.task_done()

    def _mark_started(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = "running"
            job.started_at = time.time()

    def _mark_finished(self, job_id: str, result: dict, exit_code: int) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            job.exit_code = exit_code
            job.state = "done" if exit_code == 0 else "error"
            job.error = None if exit_code == 0 else str(result.get("error") or result)
            job.finished_at = time.time()

    def _mark_failed(self, job_id: str, exc: Exception) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.exit_code = 1
            job.finished_at = time.time()
