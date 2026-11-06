from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from careflow_desktop.api.events import EventStream
from careflow_desktop.config import Config
from careflow_desktop.state import AppState, desk_hospitals
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.appointments import AppointmentsView
from careflow_desktop.ui.admin import AdminView
from careflow_desktop.ui.audit import AuditView
from careflow_desktop.ui.dashboard import DashboardView
from careflow_desktop.ui.doctors import DoctorsView
from careflow_desktop.ui.login import LoginView
from careflow_desktop.ui.patients import PatientsView
from careflow_desktop.ui.queue import QueueView
from careflow_desktop.ui.widgets import error_text

log = logging.getLogger("careflow.desktop")

CONNECTION_TEXT = {"live": "● Live updates on", "connecting": "● Connecting…", "offline": "● Offline – refreshing periodically"}


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, config: Config):
        super().__init__()
        self.state = state
        self.config = config
        self.stream: EventStream | None = None
        self.setWindowTitle("CareFlow – Hospital Desk")
        self.resize(1360, 820)

        self.pages = QStackedWidget()
        self.login = LoginView(state)
        self.workspace = self._build_workspace()
        self.pages.addWidget(self.login)
        self.pages.addWidget(self.workspace)
        self.setCentralWidget(self.pages)

        self.connection = QLabel(objectName="connection")
        self.statusBar().addPermanentWidget(self.connection)

        self.login.logged_in.connect(self._on_logged_in)
        state.status_message.connect(lambda msg: self.statusBar().showMessage(msg, 8000))
        state.session_expired.connect(self._on_session_expired)
        state.stream_state.connect(self._on_stream_state)
        # Called from worker threads; the signal hops to the UI thread.
        state.client.on_session_expired = state.session_expired.emit

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._periodic_refresh)
        self.pages.setCurrentWidget(self.login)
        self.login.reset()

    # --- Layout ---------------------------------------------------------------------------

    def _build_workspace(self) -> QWidget:
        root = QWidget()
        top = QHBoxLayout()
        self.hospital = QComboBox()
        self.hospital.setAccessibleName("Hospital")
        self.user_label = QLabel(objectName="subtle")
        logout = QPushButton("Sign out")
        top.addWidget(QLabel("CareFlow", objectName="brand"))
        top.addWidget(self.hospital)
        top.addStretch()
        top.addWidget(self.user_label)
        top.addWidget(logout)

        self.nav = QListWidget(objectName="nav")
        self.nav.setFixedWidth(190)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.views = QStackedWidget()
        self.view_list = [DashboardView(self.state), AppointmentsView(self.state), QueueView(self.state),
                          PatientsView(self.state), DoctorsView(self.state), AuditView(self.state),
                          AdminView(self.state)]
        for i, view in enumerate(self.view_list, start=1):
            self.nav.addItem(view.title)
            self.nav.item(i - 1).setToolTip(f"{view.title} (Ctrl+{i})")
            self.views.addWidget(view)
            QShortcut(QKeySequence(f"Ctrl+{i}"), root, activated=lambda i=i: self.nav.setCurrentRow(i - 1))
        self.nav.currentRowChanged.connect(self.views.setCurrentIndex)
        QShortcut(QKeySequence.Refresh, root, activated=lambda: self.views.currentWidget().refresh())

        body = QHBoxLayout()
        body.addWidget(self.nav)
        body.addWidget(self.views, 1)
        layout = QVBoxLayout(root)
        layout.addLayout(top)
        layout.addLayout(body, 1)

        logout.clicked.connect(self._logout)
        self.hospital.currentIndexChanged.connect(self._hospital_selected)
        return root

    # --- Session --------------------------------------------------------------------------

    def _on_logged_in(self, user: dict) -> None:
        self.state.user = user
        self.state.hospitals = desk_hospitals(user)
        self.user_label.setText(f"{user['full_name']}")
        self.hospital.blockSignals(True)
        self.hospital.clear()
        for h in self.state.hospitals:
            self.hospital.addItem(h.name, h.id)
        self.hospital.setVisible(len(self.state.hospitals) > 1)
        self.hospital.blockSignals(False)
        self.pages.setCurrentWidget(self.workspace)
        self.nav.setCurrentRow(0)
        self._hospital_selected()
        self._start_stream()
        self._poll.start(self.config.refresh_interval_s * 1000)

    def _hospital_selected(self) -> None:
        index = max(self.hospital.currentIndex(), 0)
        if not self.state.hospitals:
            return
        hospital = self.state.hospitals[index]
        client = self.state.client

        def load():
            details = client.get(f"/hospitals/{hospital.id}")
            return details, client.doctors(hospital.id)

        def done(result):
            details, doctors = result
            hospital.timezone = details["timezone"]
            self.state.hospital = hospital
            self.state.doctors = doctors
            self.setWindowTitle(f"CareFlow – {hospital.name}")
            self.state.hospital_changed.emit()

        def failed(exc):
            self.statusBar().showMessage(f"Could not load hospital details: {error_text(exc)}", 10000)
            QTimer.singleShot(5000, self._hospital_selected)  # keep trying; the server may be restarting

        run_task(load, done, failed, owner=self)

    def _start_stream(self) -> None:
        self._stop_stream()
        self.stream = EventStream(self.state.client)
        self.stream.event_received.connect(self.state.server_event.emit)
        self.stream.state_changed.connect(self.state.stream_state.emit)
        self.stream.start()

    def _stop_stream(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.wait(3000)
            self.stream = None

    def _on_stream_state(self, state: str) -> None:
        self.connection.setText(CONNECTION_TEXT.get(state, state))
        self.connection.setProperty("state", state)
        self.connection.style().unpolish(self.connection)
        self.connection.style().polish(self.connection)
        # Poll more often while live updates are unavailable.
        if self.pages.currentWidget() is self.workspace:
            interval = self.config.refresh_interval_s if state == "live" else 15
            self._poll.start(interval * 1000)
            if state == "live":
                self._periodic_refresh()  # catch up on anything missed while disconnected

    def _periodic_refresh(self) -> None:
        current = self.views.currentWidget()
        if current is not None and self.state.hospital is not None:
            current.schedule_refresh()

    def _logout(self) -> None:
        self._end_session("You have signed out.")
        run_task(self.state.client.logout)

    def _on_session_expired(self) -> None:
        if self.pages.currentWidget() is self.workspace:
            self._end_session("Your session has ended. Please sign in again.")

    def _end_session(self, message: str) -> None:
        self._poll.stop()
        self._stop_stream()
        self.state.hospital = None
        self.state.user = None
        self.connection.clear()
        self.pages.setCurrentWidget(self.login)
        self.login.reset(message)

    def closeEvent(self, event) -> None:
        self._stop_stream()
        super().closeEvent(event)

