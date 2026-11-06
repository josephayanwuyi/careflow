"""Base class for the main screens: refresh handling, live-update reaction, and running actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from careflow_desktop.api.errors import ApiError
from careflow_desktop.state import AppState
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.widgets import show_error


class BaseView(QWidget):
    title = ""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self._dirty = True
        self._debounce = QTimer(self, singleShot=True, interval=400)
        self._debounce.timeout.connect(self.refresh)
        state.server_event.connect(self._on_server_event)
        state.hospital_changed.connect(self._on_hospital_changed)

    # --- Refresh lifecycle -------------------------------------------------------------

    def refresh(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def is_relevant(self, event: dict) -> bool:
        return False

    def schedule_refresh(self) -> None:
        """Refresh soon if visible; otherwise refresh when the user next opens this screen."""
        if self.isVisible():
            self._debounce.start()
        else:
            self._dirty = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self._dirty = False
            self.refresh()

    def _on_server_event(self, event: dict) -> None:
        if self.is_relevant(event):
            self.schedule_refresh()

    def _on_hospital_changed(self) -> None:
        self.reset()
        self.schedule_refresh()

    def reset(self) -> None:
        """Clear hospital-specific UI state when the selected hospital changes."""

    # --- Actions ------------------------------------------------------------------------

    def notify(self, message: str) -> None:
        self.state.status_message.emit(message)

    def run_action(self, fn: Callable[[], Any], success_message: str,
                   on_success: Callable[[Any], None] | None = None) -> None:
        """Run a state-changing API call; report success in the status bar, errors in a dialog."""

        def done(result):
            self.notify(success_message)
            if on_success:
                on_success(result)
            self.refresh()

        def failed(exc):
            show_error(self, exc)
            if isinstance(exc, ApiError) and exc.code in ("stale_version", "invalid_transition"):
                self.refresh()  # someone else changed it: show the current state

        run_task(fn, done, failed, owner=self)
