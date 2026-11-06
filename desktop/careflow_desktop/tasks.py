"""Run blocking API calls off the UI thread so the interface never freezes.

`run_task(fn, on_success, on_error, owner)` executes `fn()` on Qt's thread pool and delivers
the result back on the UI thread. If `owner` (a widget) has been destroyed by then, the
callbacks are skipped. Unexpected exceptions are logged and passed to `on_error`; they are
never swallowed silently.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import shiboken6
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger("careflow.desktop.tasks")


class _Emitter(QObject):
    succeeded = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], emitter: _Emitter):
        super().__init__()
        self._fn = fn
        self._emitter = emitter

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # delivered to on_error on the UI thread
            self._emitter.failed.emit(exc)
        else:
            self._emitter.succeeded.emit(result)


_in_flight: set[_Emitter] = set()


def run_task(fn: Callable[[], Any], on_success: Callable[[Any], None] | None = None,
             on_error: Callable[[Exception], None] | None = None, owner: QObject | None = None) -> None:
    emitter = _Emitter()  # created on the UI thread, so its signals are delivered there

    def alive() -> bool:
        return owner is None or shiboken6.isValid(owner)

    def done(result):
        _in_flight.discard(emitter)
        if on_success and alive():
            on_success(result)

    def fail(exc):
        _in_flight.discard(emitter)
        from careflow_desktop.api.errors import ApiError

        if not isinstance(exc, ApiError):
            log.error("Unexpected error in background task", exc_info=exc)
        if on_error and alive():
            on_error(exc)

    emitter.succeeded.connect(done)
    emitter.failed.connect(fail)
    _in_flight.add(emitter)
    QThreadPool.globalInstance().start(_Task(fn, emitter))


def pending_tasks() -> int:
    return len(_in_flight)
