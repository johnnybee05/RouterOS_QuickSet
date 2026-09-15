"""Run blocking network work in QThreads so the UI never freezes; cooperative cancel."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot


class CancelToken:
    """Shared between the UI and a task. Cancel callbacks can close sockets to unblock I/O."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancelled(self) -> bool:
        return self._event.is_set()

    def on_cancel(self, callback: Callable[[], None]) -> None:
        with self._lock:
            run_now = self._event.is_set()
            if not run_now:
                self._callbacks.append(callback)
        if run_now:
            callback()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            # Closing an already broken socket may raise; cancellation must still complete.
            with contextlib.suppress(Exception):
                callback()


Task = Callable[[CancelToken, Callable[[object], None]], Any]


class _Worker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, task: Task, token: CancelToken) -> None:
        super().__init__()
        self.task = task
        self.token = token

    @Slot()
    def run(self) -> None:
        try:
            result = self.task(self.token, self.progress.emit)
        except BaseException as exc:
            self.failed.emit(exc)
        else:
            self.finished.emit(result)


class _Relay(QObject):
    """Created in the UI thread; delivers worker results to plain callbacks there."""

    def __init__(
        self,
        on_progress: Callable[[object], None],
        on_finished: Callable[[Any], None],
        on_failed: Callable[[BaseException], None],
        on_thread_finished: Callable[[], None],
    ) -> None:
        super().__init__()
        self._progress = on_progress
        self._finished = on_finished
        self._failed = on_failed
        self._thread_finished = on_thread_finished

    @Slot(object)
    def on_progress(self, value: object) -> None:
        self._progress(value)

    @Slot(object)
    def on_finished(self, value: object) -> None:
        self._finished(value)

    @Slot(object)
    def on_failed(self, exc: object) -> None:
        self._failed(exc)  # type: ignore[arg-type]

    @Slot()
    def on_thread_finished(self) -> None:
        self._thread_finished()


class TaskRunner(QObject):
    """Starts tasks on their own QThread and keeps them alive until they finish."""

    busyChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running: dict[QThread, tuple[_Worker, _Relay, CancelToken]] = {}

    @property
    def busy(self) -> bool:
        return bool(self._running)

    def start(
        self,
        task: Task,
        *,
        on_finished: Callable[[Any], None] = lambda result: None,
        on_failed: Callable[[BaseException], None] = lambda exc: None,
        on_progress: Callable[[object], None] = lambda event: None,
    ) -> CancelToken:
        token = CancelToken()
        thread = QThread()
        worker = _Worker(task, token)
        worker.moveToThread(thread)
        # Plain Python callables connected directly would run in the worker thread. The relay
        # lives in this (UI) thread, so its slots receive the signals as queued calls.
        relay = _Relay(on_progress, on_finished, on_failed, lambda: self._cleanup(thread))
        worker.progress.connect(relay.on_progress)
        worker.finished.connect(relay.on_finished)
        worker.failed.connect(relay.on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(relay.on_thread_finished)
        was_busy = self.busy
        self._running[thread] = (worker, relay, token)
        thread.start()
        if not was_busy:
            self.busyChanged.emit(True)
        return token

    def _cleanup(self, thread: QThread) -> None:
        self._running.pop(thread, None)
        thread.deleteLater()
        if not self._running:
            self.busyChanged.emit(False)

    def cancel_all(self) -> None:
        for *_, token in list(self._running.values()):
            token.cancel()

    def wait_all(self, timeout_ms: int = 5000) -> bool:
        return all(thread.wait(timeout_ms) for thread in list(self._running))
