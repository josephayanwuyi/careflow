from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from careflow_desktop.formatting import event_label, local_time, status_label, wait_text
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.dialogs import DoctorChoiceDialog, PriorityDialog, ReasonDialog
from careflow_desktop.ui.models import Column, RecordTableModel
from careflow_desktop.ui.patients import PatientPickerDialog
from careflow_desktop.ui.widgets import RecordTable, StateStack, confirm, error_text, section_title


class QueueView(BaseView):
    title = "Live Queue"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._queues: dict[str, dict] = {}   # doctor_id -> queue summary for today
        self._queue: dict | None = None

        # Header: doctor selection and queue-level actions
        self.doctor = QComboBox()
        self.doctor.setMinimumWidth(260)
        doctor_label = QLabel("&Doctor")
        doctor_label.setBuddy(self.doctor)
        self.doctor_status = QLabel(objectName="badgeLabel")
        self.paused_label = QLabel(objectName="warningText")
        self.include_finished = QCheckBox("Show finished")
        self.btn_walk_in = QPushButton("Add &walk-in…")
        self.btn_call = QPushButton("Call &next")
        self.btn_pause = QPushButton("&Pause queue…")
        refresh = QPushButton("Refresh")
        header = QHBoxLayout()
        header.addWidget(doctor_label)
        header.addWidget(self.doctor)
        header.addWidget(self.doctor_status)
        header.addWidget(self.paused_label)
        header.addStretch()
        header.addWidget(self.include_finished)
        header.addWidget(self.btn_walk_in)
        header.addWidget(self.btn_call)
        header.addWidget(self.btn_pause)
        header.addWidget(refresh)

        # Entries table
        tz = lambda: self.state.hospital.timezone if self.state.hospital else "UTC"  # noqa: E731
        self.table = RecordTable(RecordTableModel([
            Column("Ticket", lambda r: r["ticket_number"], align=Qt.AlignCenter),
            Column("Position", lambda r: r["position"] or "", align=Qt.AlignCenter,
                   sort=lambda r: r["position"] or 999),
            Column("Patient", lambda r: r["patient"]["full_name"]),
            Column("Hospital no.", lambda r: r["patient"].get("hospital_patient_number") or ""),
            Column("Status", lambda r: r["status"], status=lambda r: r["status"]),
            Column("Type", lambda r: "Appointment" if r["kind"] == "appointment" else "Walk-in"),
            Column("Priority", lambda r: r["priority_level"] or "", align=Qt.AlignCenter,
                   tooltip=lambda r: r.get("priority_reason")),
            Column("Checked in", lambda r: local_time(r["checked_in_at"], tz()), sort=lambda r: r["checked_in_at"]),
            Column("Arrival", lambda r: "Confirmed" if r.get("arrival_confirmed_at") else "Self check-in (unconfirmed)"),
            Column("Estimated wait", lambda r: wait_text(r["estimate"]),
                   tooltip=lambda r: r["estimate"].get("explanation")),
        ]), sortable=False)
        self.table.setAccessibleName("Queue entries")
        self.stack = StateStack(self.table)

        # Selected entry actions and history
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(section_title("Selected patient"))
        self.selected_label = QLabel("Select a patient in the queue.", wordWrap=True)
        sl.addWidget(self.selected_label)
        self.btn_confirm = QPushButton("&Confirm arrival")
        self.btn_return = QPushButton("&Return to queue…")
        self.btn_transfer = QPushButton("&Transfer to another doctor…")
        self.btn_priority = QPushButton("Change p&riority…")
        self.btn_remove = QPushButton("Remove from &queue…")
        self.btn_more = QToolButton(text="Consultation actions", popupMode=QToolButton.InstantPopup)
        self._more_menu = QMenu(self)
        self.act_start = QAction("Start consultation", self)
        self.act_complete = QAction("Complete consultation", self)
        self.act_missed = QAction("Mark missed call…", self)
        self.act_recall = QAction("Call again", self)
        for act in (self.act_recall, self.act_start, self.act_complete, self.act_missed):
            self._more_menu.addAction(act)
        self.btn_more.setMenu(self._more_menu)
        for b in (self.btn_confirm, self.btn_return, self.btn_transfer, self.btn_priority, self.btn_remove,
                  self.btn_more):
            sl.addWidget(b)
        sl.addWidget(section_title("Events"))
        self.events = QListWidget()
        self.events.setAccessibleName("Queue entry events")
        sl.addWidget(self.events, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.stack)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)

        self.doctor.currentIndexChanged.connect(self._doctor_changed)
        self.include_finished.toggled.connect(self.refresh)
        refresh.clicked.connect(self.refresh)
        self.btn_walk_in.clicked.connect(self._walk_in)
        self.btn_call.clicked.connect(self._call_next)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.btn_confirm.clicked.connect(lambda: self._entry_action("confirm-arrival", "Arrival confirmed."))
        self.btn_return.clicked.connect(self._return)
        self.btn_transfer.clicked.connect(self._transfer)
        self.btn_priority.clicked.connect(self._priority)
        self.btn_remove.clicked.connect(self._remove)
        self.act_start.triggered.connect(lambda: self._entry_action("start", "Consultation started."))
        self.act_complete.triggered.connect(lambda: self._entry_action("complete", "Consultation completed."))
        self.act_recall.triggered.connect(lambda: self._entry_action("recall", "Patient called again."))
        self.act_missed.triggered.connect(self._missed)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._call_next)
        self._update_entry_actions(None)

    # --- Loading ----------------------------------------------------------------------------

    def reset(self) -> None:
        self._queues, self._queue = {}, None
        self.doctor.blockSignals(True)
        self.doctor.clear()
        self.doctor.blockSignals(False)
        self.table.record_model.set_rows([])

    def is_relevant(self, event: dict) -> bool:
        return event.get("type") in ("queue_entry", "queue", "doctor_status")

    def refresh(self) -> None:
        if self.state.hospital is None:
            return
        self._fill_doctors()
        hid = self.state.hospital_id
        run_task(lambda: self.state.client.queues(hid), self._got_queues,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    def _fill_doctors(self) -> None:
        if self.doctor.count() == len(self.state.doctors):
            return
        current = self.doctor.currentData()
        self.doctor.blockSignals(True)
        self.doctor.clear()
        for d in self.state.doctors:
            self.doctor.addItem(d["display_name"], d["id"])
        index = self.doctor.findData(current)
        self.doctor.setCurrentIndex(max(index, 0))
        self.doctor.blockSignals(False)

    def _got_queues(self, queues: list[dict]) -> None:
        self._queues = {q["doctor"]["id"]: q for q in queues}
        for i in range(self.doctor.count()):
            q = self._queues.get(self.doctor.itemData(i))
            name = self.state.doctor_name(self.doctor.itemData(i))
            waiting = q["counts"].get("waiting", 0) if q else 0
            self.doctor.setItemText(i, f"{name}  ({waiting} waiting)" if q else name)
        self._load_entries()

    def _doctor_changed(self) -> None:
        self.table.record_model.set_rows([])
        self._load_entries()

    def _load_entries(self) -> None:
        doctor_id = self.doctor.currentData()
        self._queue = self._queues.get(doctor_id)
        self._update_header()
        if doctor_id is None:
            self.stack.show_empty("No doctors are registered at this hospital.")
            return
        if self._queue is None:
            self.stack.show_empty(f"No patients have joined {self.state.doctor_name(doctor_id)}'s queue today.\n"
                                  "Check in an appointment or add a walk-in.")
            return
        self.stack.show_loading()
        queue_id, finished = self._queue["id"], self.include_finished.isChecked()

        def done(entries):
            if not self._queue or self._queue["id"] != queue_id:
                return
            self.table.set_rows_keep_selection(entries)
            if entries:
                self.stack.show_content()
            else:
                self.stack.show_empty("Nobody is waiting in this queue.")
            self._selection_changed()

        run_task(lambda: self.state.client.queue_entries(queue_id, finished), done,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    def _update_header(self) -> None:
        q = self._queue
        doctor = next((d for d in self.state.doctors if d["id"] == self.doctor.currentData()), None)
        status = q["doctor"]["public_status"] if q else None
        self.doctor_status.setText(f"Doctor: {status_label(status)}" if status else "")
        paused = bool(q and q["is_paused"])
        reason = q.get("paused_reason") if q else None
        self.paused_label.setText(f"Queue paused{': ' + reason if reason else ''}" if paused else "")
        self.btn_pause.setText("Resume &queue" if paused else "&Pause queue…")
        self.btn_pause.setEnabled(q is not None)
        self.btn_call.setEnabled(q is not None and not paused and q["counts"].get("waiting", 0) > 0)
        self.btn_walk_in.setEnabled(doctor is not None and self.state.can("queue.manage"))

    # --- Selected entry ------------------------------------------------------------------------

    def _selection_changed(self) -> None:
        entry = self.table.selected_record()
        self._update_entry_actions(entry)
        self.events.clear()
        if entry is None:
            self.selected_label.setText("Select a patient in the queue.")
            return
        est = entry["estimate"]
        self.selected_label.setText(
            f"<b>{entry['patient']['full_name']}</b> — ticket {entry['ticket_number']}<br>"
            f"{status_label(entry['status'])}. {est.get('explanation', '')}"
            + (f"<br>Reason for visit: {entry['reason_for_visit']}" if entry.get("reason_for_visit") else ""))
        entry_id, tz = entry["id"], self.state.hospital.timezone

        def done(events):
            current = self.table.selected_record()
            if not current or current["id"] != entry_id:
                return
            self.events.clear()
            for e in events:
                text = f"{local_time(e['occurred_at'], tz)} {event_label(e['event_type'])} ({e.get('actor_role') or 'system'})"
                if e.get("reason"):
                    text += f": {e['reason']}"
                self.events.addItem(text)

        run_task(lambda: self.state.client.queue_entry_events(entry_id), done,
                 lambda exc: self.events.addItem(error_text(exc)), owner=self)

    def _update_entry_actions(self, entry: dict | None) -> None:
        actions = set(entry["allowed_actions"]) if entry else set()
        self.btn_confirm.setEnabled("confirm_arrival" in actions and not (entry or {}).get("arrival_confirmed_at"))
        self.btn_return.setEnabled("return" in actions)
        self.btn_transfer.setEnabled("transfer" in actions)
        self.btn_priority.setEnabled("set_priority" in actions and self.state.can("queue.priority.set"))
        self.btn_remove.setEnabled("cancel" in actions)
        self.act_start.setEnabled("start" in actions)
        self.act_complete.setEnabled("complete" in actions)
        self.act_missed.setEnabled("mark_missed" in actions)
        self.act_recall.setEnabled("recall" in actions)
        self.btn_more.setEnabled(bool(actions & {"start", "complete", "mark_missed", "recall"}))

    # --- Actions ------------------------------------------------------------------------------

    def _entry_action(self, action: str, message: str, **body) -> None:
        entry = self.table.selected_record()
        if entry:
            self.run_action(lambda: self.state.client.entry_action(entry["id"], action,
                                                                   expected_version=entry["version"], **body), message)

    def _call_next(self) -> None:
        if self._queue and self.btn_call.isEnabled():
            queue_id = self._queue["id"]
            self.run_action(lambda: self.state.client.call_next(queue_id), "Next patient called.",
                            on_success=lambda e: self.table.select_key(e["id"]))

    def _toggle_pause(self) -> None:
        if not self._queue:
            return
        queue_id = self._queue["id"]
        if self._queue["is_paused"]:
            self.run_action(lambda: self.state.client.set_queue_paused(queue_id, False), "Queue resumed.")
            return
        dialog = ReasonDialog("Pause queue", "Pausing stops patients being called and shows patients that waiting "
                              "times are unavailable.", "Pause queue", required=False, parent=self)
        if dialog.exec():
            self.run_action(lambda: self.state.client.set_queue_paused(queue_id, True, dialog.value()),
                            "Queue paused.")

    def _walk_in(self) -> None:
        doctor_id = self.doctor.currentData()
        dialog = PatientPickerDialog(self.state, f"Add a walk-in to {self.state.doctor_name(doctor_id)}'s queue",
                                     "Add to queue", parent=self)
        if dialog.exec() and dialog.patient:
            patient = dialog.patient
            hid = self.state.hospital_id
            self.run_action(lambda: self.state.client.add_walk_in(hid, patient_id=patient["id"], doctor_id=doctor_id),
                            f"{patient['full_name']} added to the queue.",
                            on_success=lambda e: self.table.select_key(e["id"]))

    def _return(self) -> None:
        entry = self.table.selected_record()
        if not entry:
            return
        dialog = ReasonDialog("Return to queue", f"{entry['patient']['full_name']} missed their call. They will "
                              "rejoin at the back of the queue.", "Return to queue", required=False, parent=self)
        if dialog.exec():
            self._entry_action("return", "Patient returned to the queue.", reason=dialog.value())

    def _missed(self) -> None:
        entry = self.table.selected_record()
        if not entry:
            return
        dialog = ReasonDialog("Missed call", f"Record that {entry['patient']['full_name']} did not respond to the call.",
                              "Mark missed", required=False, parent=self)
        if dialog.exec():
            self._entry_action("mark-missed", "Marked as missed call.", reason=dialog.value())

    def _transfer(self) -> None:
        entry = self.table.selected_record()
        if not entry:
            return
        dialog = DoctorChoiceDialog(self.state.doctors, "Transfer patient",
                                    f"Move {entry['patient']['full_name']} to another doctor's queue today. "
                                    "They keep their place in line.", "Transfer",
                                    exclude=self.doctor.currentData(), parent=self)
        if dialog.exec():
            self._entry_action("transfer", "Patient transferred.", doctor_id=dialog.doctor_id(),
                               reason=dialog.reason_text())

    def _priority(self) -> None:
        entry = self.table.selected_record()
        if not entry:
            return
        dialog = PriorityDialog(entry["priority_level"], parent=self)
        if dialog.exec():
            self._entry_action("priority", "Priority updated.", level=dialog.level.value(),
                               reason=dialog.reason.toPlainText().strip())

    def _remove(self) -> None:
        entry = self.table.selected_record()
        if not entry:
            return
        dialog = ReasonDialog("Remove from queue", f"Remove {entry['patient']['full_name']} from the queue? "
                              "If they have an appointment it will be cancelled.", "Remove", parent=self)
        if dialog.exec() and confirm(self, "Confirm removal", "This cannot be undone. Continue?", "Remove"):
            self._entry_action("cancel", "Patient removed from the queue.", reason=dialog.value())
