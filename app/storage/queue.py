"""Serialised write queue with lock-aware retry.

All workbook writes go through ONE dedicated background thread, ensuring
concurrent requests never interleave inside the Excel workbook.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.storage.excel import WorkbookLocked

log = logging.getLogger("tony.storage.queue")


@dataclass
class Job:
    run: Callable[[], Any]
    on_done: Callable[[Any], None]
    on_error: Callable[[Exception], None]
    on_queued: Callable[[], None]
    enqueued: float = field(default_factory=time.time)
    notified: bool = False


class WriteQueue:
    def __init__(self, is_locked: Callable[[], bool], retry_seconds: float, max_wait: float):
        self.is_locked = is_locked
        self.retry = retry_seconds
        self.max_wait = max_wait
        self._q: "queue.Queue[Job]" = queue.Queue()
        self._pending = 0
        self._cv = threading.Condition()
        self._t = threading.Thread(target=self._worker, name="tony-writer", daemon=True)
        self._t.start()

    @property
    def depth(self) -> int:
        return self._pending

    def submit(self, job: Job):
        with self._cv:
            self._pending += 1
        if self.is_locked():
            self._notify(job)
        self._q.put(job)

    def wait_idle(self, timeout: float = 60) -> bool:
        end = time.time() + timeout
        with self._cv:
            while self._pending > 0:
                left = end - time.time()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    @staticmethod
    def _notify(job: Job):
        if not job.notified:
            job.notified = True
            try:
                job.on_queued()
            except Exception:
                log.exception("on_queued failed")

    def _finish(self):
        with self._cv:
            self._pending -= 1
            self._cv.notify_all()

    def _worker(self):
        while True:
            job = self._q.get()
            while True:
                try:
                    result = job.run()
                except WorkbookLocked:
                    self._notify(job)
                    if time.time() - job.enqueued > self.max_wait:
                        self._safe(job.on_error, WorkbookLocked("workbook stayed locked too long"))
                        break
                    log.warning("workbook locked; retrying in %ss", self.retry)
                    time.sleep(self.retry)
                    continue
                except Exception as e:
                    log.exception("write job failed")
                    self._safe(job.on_error, e)
                    break
                else:
                    self._safe(job.on_done, result)
                    break
            self._finish()

    @staticmethod
    def _safe(fn, arg):
        try:
            fn(arg)
        except Exception:
            log.exception("callback failed")
