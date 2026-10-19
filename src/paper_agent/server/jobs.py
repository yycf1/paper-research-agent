"""Durable background jobs. Interrupted work is recorded, never silently resumed."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"
    result: dict = field(default_factory=dict)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self.__dict__, ensure_ascii=False))


class _Progress(dict):
    def __init__(self, value, changed):
        super().__init__(value)
        self.changed = changed

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self.changed()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.changed()


class JobRegistry:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            for (payload,) in conn.execute("SELECT payload FROM jobs ORDER BY rowid"):
                job = Job(**json.loads(payload))
                self._jobs[job.id] = job
        for job in self._jobs.values():
            if job.status == "running":
                job.status = "interrupted"
                job.finished_at = time.time()
                job.error = "服务在任务完成前停止；已保留逐篇结果，可重试未完成项。"
                for item in job.result.get("items", []):
                    if item.get("status") in {"pending", "running"}:
                        item.update(status="interrupted", detail="服务停止，未确认下载完成；重试时会检查本地文件")
                self.save(job)

    def save(self, job: Job) -> None:
        with self._lock, sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)",
                         (job.id, json.dumps(job.to_dict(), ensure_ascii=False)))
            self._jobs[job.id] = job

    def submit(self, kind: str, fn: Callable[[dict], dict], *, result: dict | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, started_at=time.time())
        job.result = _Progress(result or {}, lambda: self.save(job))
        self.save(job)

        def run():
            try:
                extra = fn(job.result) or {}
                job.result.update(extra)
                job.status = "done"
            except Exception as exc:
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"[:1000]
            finally:
                job.finished_at = time.time()
                self.save(job)

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())


_REGISTRIES: dict[str, JobRegistry] = {}
_LOCK = threading.Lock()


def registry_for(cfg: dict) -> JobRegistry:
    from paper_agent import paths
    path = (paths.data_dir(cfg) / "jobs.db").resolve()
    with _LOCK:
        if str(path) not in _REGISTRIES:
            _REGISTRIES[str(path)] = JobRegistry(path)
        return _REGISTRIES[str(path)]
