from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from careflow_desktop.formatting import event_label, local, status_label
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.dialogs import ReasonDialog, SlotPickerDialog
from careflow_desktop.ui.models import Column, RecordTableModel
from careflow_desktop.ui.widgets import RecordTable, StateStack, confirm, error_text, section_title

STATUS_FILTERS = [
    ("Pending approval", ["requested"]),
    ("Approved", ["approved"]),
    ("New time proposed", ["reschedule_proposed"]),
    ("Checked in", ["checked_in"]),
    ("Completed", ["completed"]),
    ("Cancelled, rejected, expired, no-show", ["cancelled", "rejected", "expired", "no_show"]),
    ("All statuses", None),
]

DATE_FILTERS = [
    ("Any date", None),
    ("Today", (0, 0)),
    ("Tomorrow", (1, 1)),
    ("Next 7 days", (0, 6)),
    ("Past 7 days", (-7, 0)),
]

PAGE_SIZE = 100


class AppointmentsView(BaseView):
    title = "Appointments"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._next_cursor: str | None = None

        # Filters
        self.status = QComboBox()
        for label, value in STATUS_FILTERS:
            self.status.addItem(label, value)
        self.doctor = QComboBox()
        self.dates = QComboBox()
        for label, value in DATE_FILTERS:
            self.dates.addItem(label, value)
        self.search = QLineEdit(placeholderText="Filter loaded rows by patient, number or doctor  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Reload (F5)")
        filters = QHBoxLayout()
        for label, widget in (("Status", self.status), ("Doctor", self.doctor), ("Date", self.dates)):
            lbl = QLabel(label)
            lbl.setBuddy(widget)
            filters.addWidget(lbl)
            filters.addWidget(widget)
        filters.addWidget(self.search, 1)
        filters.addWidget(refresh)

        # Table
        tz = lambda: self.state.hospital.timezone if self.state.hospital else "UTC"  # noqa: E731
        self.table = RecordTable(RecordTableModel([
            Column("Time", lambda r: local(r["start_at"], tz()), sort=lambda r: r["start_at"]),
            Column("Patient", lambda r: r["patient"]["full_name"] if r.get("patient") else ""),
            Column("Hospital no.", lambda r: (r.get("patient") or {}).get("hospital_patient_number") or ""),
            Column("Doctor", lambda r: r["doctor_name"]),
            Column("Department", lambda r: r["department_name"]),
            Column("Status", lambda r: r["status"], status=lambda r: r["status"]),
            Column("Source", lambda r: "Patient request" if r["source"] == "patient_request" else "Booked by staff"),
            Column("Received", lambda r: local(r["created_at"], tz()), sort=lambda r: r["created_at"]),
        ]))
        self.table.setAccessibleName("Appointments")
        self.load_more = QPushButton("Load more")
        self.load_more.hide()
        self.count_label = QLabel(objectName="subtle")
        table_box = QWidget()
        tl = QVBoxLayout(table_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.table)
        bottom = QHBoxLayout()
        bottom.addWidget(self.count_label)
        bottom.addStretch()
        bottom.addWidget(self.load_more)
        tl.addLayout(bottom)
        self.stack = StateStack(table_box)

        # Details panel
        self.details = self._build_details()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.stack)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(splitter, 1)

        # Signals
        for combo in (self.status, self.doctor, self.dates):
            combo.currentIndexChanged.connect(self.refresh)
        refresh.clicked.connect(self.refresh)
        self.search.textChanged.connect(self.table.proxy.setFilterFixedString)
        self.load_more.clicked.connect(self._load_more)
        self.table.selectionModel().selectionChanged.connect(self._show_selected)
        QShortcut(QKeySequence.Find, self, activated=self.search.setFocus)

    # --- Details panel ---------------------------------------------------------------------

    def _build_details(self) -> QWidget:
        panel = QFrame(objectName="detailsPanel")
        layout = QVBoxLayout(panel)
        layout.addWidget(section_title("Appointment details"))
        form = QFormLayout()
        self.d = {k: QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse) for k in (
            "patient", "doctor", "time", "status", "proposed", "reason_for_visit", "status_reason", "source")}
        for key, label in (("patient", "Patient"), ("doctor", "Doctor"), ("time", "Time"), ("status", "Status"),
                           ("proposed", "Proposed time"), ("reason_for_visit", "Reason for visit"),
                           ("status_reason", "Decision note"), ("source", "Source")):
            form.addRow(label, self.d[key])
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.btn_approve = QPushButton("&Approve")
        self.btn_reject = QPushButton("&Reject…")
        self.btn_propose = QPushButton("Propose &new time…")
        buttons.addWidget(self.btn_approve)
        buttons.addWidget(self.btn_reject)
        buttons.addWidget(self.btn_propose)
        layout.addLayout(buttons)
        buttons2 = QHBoxLayout()
        self.btn_checkin = QPushButton("Check &in")
        self.btn_cancel = QPushButton("&Cancel appointment…")
        self.btn_noshow = QPushButton("Mark no-&show…")
        buttons2.addWidget(self.btn_checkin)
        buttons2.addWidget(self.btn_cancel)
        buttons2.addWidget(self.btn_noshow)
        layout.addLayout(buttons2)

        layout.addWidget(section_title("History"))
        self.history = QListWidget()
        self.history.setAccessibleName("Appointment history")
        layout.addWidget(self.history, 1)

        self.btn_approve.clicked.connect(self._approve)
        self.btn_reject.clicked.connect(self._reject)
        self.btn_propose.clicked.connect(self._propose)
        self.btn_checkin.clicked.connect(self._check_in)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_noshow.clicked.connect(self._no_show)
        self._set_actions([])
        return panel

    def _set_actions(self, actions: list[str]) -> None:
        self.btn_approve.setEnabled("approve" in actions)
        self.btn_reject.setEnabled("reject" in actions)
        self.btn_propose.setEnabled("propose_reschedule" in actions)
        self.btn_checkin.setEnabled("check_in" in actions and self.state.can("queue.manage"))
        self.btn_cancel.setEnabled("cancel" in actions)
        self.btn_noshow.setEnabled("mark_no_show" in actions)

    def _show_selected(self) -> None:
        appt = self.table.selected_record()
        if appt is None:
            for label in self.d.values():
                label.clear()
            self.history.clear()
            self._set_actions([])
            return
        tz = self.state.hospital.timezone
        p = appt.get("patient") or {}
        self.d["patient"].setText(f"{p.get('full_name', '')}  ({p.get('hospital_patient_number') or 'no number'})")
        self.d["doctor"].setText(f"{appt['doctor_name']} — {appt['department_name']}")
        self.d["time"].setText(local(appt["start_at"], tz, "%A %d %B %Y, %H:%M"))
        self.d["status"].setText(status_label(appt["status"]))
        self.d["proposed"].setText(local(appt["proposed_start_at"], tz) or "—")
        self.d["reason_for_visit"].setText(appt.get("reason_for_visit") or "—")
        self.d["status_reason"].setText(appt.get("status_reason") or "—")
        self.d["source"].setText("Patient request" if appt["source"] == "patient_request" else "Booked by staff")
        self._set_actions(appt["allowed_actions"])
        self._load_history(appt["id"])

    def _load_history(self, appointment_id: str) -> None:
        self.history.clear()
        tz = self.state.hospital.timezone

        def done(entries):
            current = self.table.selected_record()
            if not current or current["id"] != appointment_id:
                return
            self.history.clear()
            for h in entries:
                who = h.get("actor_role") or "system"
                text = f"{local(h['occurred_at'], tz)} — {event_label(h['event'])} by {who}"
                if h.get("reason"):
                    text += f": {h['reason']}"
                self.history.addItem(text)

        run_task(lambda: self.state.client.appointment_history(appointment_id), done,
                 lambda exc: self.history.addItem(error_text(exc)), owner=self)

    # --- Loading --------------------------------------------------------------------------

    def reset(self) -> None:
        self.table.record_model.set_rows([])
        self.doctor.blockSignals(True)
        self.doctor.clear()
        self.doctor.blockSignals(False)

    def _fill_doctors(self) -> None:
        if self.doctor.count() == len(self.state.doctors) + 1:
            return
        current = self.doctor.currentData()
        self.doctor.blockSignals(True)
        self.doctor.clear()
        self.doctor.addItem("All doctors", None)
        for d in self.state.doctors:
            self.doctor.addItem(d["display_name"], d["id"])
        index = self.doctor.findData(current)
        self.doctor.setCurrentIndex(max(index, 0))
        self.doctor.blockSignals(False)

    def _filters(self) -> dict:
        filters = {"limit": PAGE_SIZE, "status": self.status.currentData(), "doctor_id": self.doctor.currentData()}
        span = self.dates.currentData()
        if span:
            today = datetime.now(ZoneInfo(self.state.hospital.timezone)).date()  # the hospital's day, not the PC's
            filters["date_from"] = (today + timedelta(days=span[0])).isoformat()
            filters["date_to"] = (today + timedelta(days=span[1])).isoformat()
        return filters

    def is_relevant(self, event: dict) -> bool:
        return event.get("type") in ("appointment", "queue_entry")

    def refresh(self) -> None:
        if self.state.hospital is None:
            return
        self._fill_doctors()
        self.stack.show_loading()
        hid, filters = self.state.hospital_id, self._filters()
        run_task(lambda: self.state.client.appointments(hid, **filters), self._show_page,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    def _show_page(self, page: dict) -> None:
        self._next_cursor = page["next_cursor"]
        self.table.set_rows_keep_selection(page["items"])
        self.load_more.setVisible(bool(self._next_cursor))
        self.count_label.setText(f"{len(page['items'])} shown" + (" (more available)" if self._next_cursor else ""))
        if page["items"]:
            self.stack.show_content()
        else:
            self.stack.show_empty(f"No appointments match: {self.status.currentText().lower()}, "
                                  f"{self.dates.currentText().lower()}.")
        self._show_selected()

    def _load_more(self) -> None:
        hid, filters = self.state.hospital_id, {**self._filters(), "cursor": self._next_cursor}

        def done(page):
            self._next_cursor = page["next_cursor"]
            self.table.record_model.append_rows(page["items"])
            self.load_more.setVisible(bool(self._next_cursor))
            self.count_label.setText(f"{self.table.record_model.rowCount()} shown")

        run_task(lambda: self.state.client.appointments(hid, **filters), done,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    # --- Actions --------------------------------------------------------------------------

    def _describe(self, appt: dict) -> str:
        p = appt.get("patient") or {}
        return (f"{p.get('full_name', 'Patient')} with {appt['doctor_name']} on "
                f"{local(appt['start_at'], self.state.hospital.timezone, '%a %d %b at %H:%M')}")

    def _approve(self) -> None:
        appt = self.table.selected_record()
        if not appt or not confirm(self, "Approve appointment", f"Approve the appointment for {self._describe(appt)}?",
                                   "Approve"):
            return
        self.run_action(lambda: self.state.client.appointment_action(
            appt["id"], "approve", expected_version=appt["version"]), "Appointment approved.")

    def _reject(self) -> None:
        appt = self.table.selected_record()
        if not appt:
            return
        dialog = ReasonDialog("Reject request", f"Reject the request for {self._describe(appt)}? "
                              "The patient will see the reason.", "Reject request", parent=self)
        if dialog.exec():
            self.run_action(lambda: self.state.client.appointment_action(
                appt["id"], "reject", reason=dialog.value(), expected_version=appt["version"]), "Request rejected.")

    def _propose(self) -> None:
        appt = self.table.selected_record()
        if not appt:
            return
        dialog = SlotPickerDialog(self.state, appt["doctor_id"], "Propose a new time",
                                  f"Offer {appt['patient']['full_name']} a different time with {appt['doctor_name']}. "
                                  "The patient must accept it.", "Send proposal", ask_reason=True, parent=self)
        if dialog.exec():
            self.run_action(lambda: self.state.client.appointment_action(
                appt["id"], "propose-reschedule", start_at=dialog.selected_start(), reason=dialog.reason_text(),
                expected_version=appt["version"]), "New time proposed to the patient.")

    def _check_in(self) -> None:
        appt = self.table.selected_record()
        if appt and confirm(self, "Check in patient", f"Check in {self._describe(appt)} and add them to the queue?",
                            "Check in"):
            self.run_action(lambda: self.state.client.check_in(appt["id"]),
                            "Patient checked in and added to the queue.")

    def _cancel(self) -> None:
        appt = self.table.selected_record()
        if not appt:
            return
        dialog = ReasonDialog("Cancel appointment", f"Cancel the appointment for {self._describe(appt)}? "
                              "The patient will be notified.", "Cancel appointment", parent=self)
        if dialog.exec():
            self.run_action(lambda: self.state.client.appointment_action(
                appt["id"], "cancel", reason=dialog.value(), expected_version=appt["version"]),
                "Appointment cancelled.")

    def _no_show(self) -> None:
        appt = self.table.selected_record()
        if not appt:
            return
        dialog = ReasonDialog("Mark as no-show", f"Record that the patient did not attend: {self._describe(appt)}.",
                              "Mark no-show", required=False, parent=self)
        if dialog.exec():
            self.run_action(lambda: self.state.client.appointment_action(
                appt["id"], "mark-no-show", reason=dialog.value(), expected_version=appt["version"]),
                "Appointment marked as no-show.")
