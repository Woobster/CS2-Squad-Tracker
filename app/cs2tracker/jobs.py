"""In-memory background jobs, run one at a time on a single worker thread."""
import queue
import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    id: int
    kind: str
    state: str = "running"  # 'running' | 'done' | 'error'
    done: int = 0
    total: int = 0
    stage: str | None = None
    results: list[dict] = field(default_factory=list)
    report: dict | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state, "done": self.done,
                "total": self.total, "stage": self.stage, "results": list(self.results),
                "report": self.report, "error": self.error}


class JobRunner:
    def __init__(self) -> None:
        self.jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._current: Job | None = None
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="cs2tracker-jobs").start()

    def busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.state == "running"

    def submit_if_idle(self, kind: str, fn: Callable[[Job], None]) -> int | None:
        """Like submit, but returns None instead of the running job's id."""
        with self._lock:
            if self._current is not None and self._current.state == "running":
                return None
            return self._start(kind, fn)

    def submit(self, kind: str, fn: Callable[[Job], None]) -> int:
        """Start fn(job) on the worker; if a job is already running, return its id instead."""
        with self._lock:
            if self._current is not None and self._current.state == "running":
                return self._current.id
            return self._start(kind, fn)

    def _start(self, kind: str, fn: Callable[[Job], None]) -> int:
        """Caller holds the lock."""
        job = Job(self._next_id, kind)
        self._next_id += 1
        self.jobs[job.id] = job
        self._current = job
        self._queue.put((job, fn))
        return job.id

    def get(self, job_id: int) -> Job | None:
        return self.jobs.get(job_id)

    def _work(self) -> None:
        while True:
            job, fn = self._queue.get()
            try:
                fn(job)
                outcome, error = "done", None
            except Exception as e:  # a failed job must not kill the worker
                outcome, error = "error", f"{type(e).__name__}: {e}"
            with self._lock:
                job.error = error
                job.state = outcome
