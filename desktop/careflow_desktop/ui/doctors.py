from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from careflow_desktop.formatting import local, local_time
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.dialogs import ReasonDialog
from careflow_desktop.ui.models import Column, RecordTableModel
from careflow_desktop.ui.widgets import RecordTable, StateStack, error_text

SOURCE_LABELS = {"self_reported": "Reported by doctor", "staff_verified": "Verified by staff", "system": "System"}


class DoctorsView(BaseView):
    title = "Doctors"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        tz = lambda: self.state.hospital.timezone if self.state.hospital else "UTC"  # noqa: E731
        self.table = RecordTable(RecordTableModel([
            Column("Doctor", lambda r: r["name"]),
            Column("Department", lambda r: r["department_name"]),
            Column("Status", lambda r: r["status"], status=lambda r: _badge(r)),
            Column("Shown to patients as", lambda r: r["public_status"], status=lambda r: r["public_status"]),
            Column("Source", lambda r: SOURCE_LABELS.get(r.get("status_source"), "—")),
            Column("Last update", lambda r: local_time(r.get("status_updated_at"), tz()) or "—"),
            Column("Expected back", lambda r: local_time(r.get("expected_return_at"), tz()) or ""),
            Column("Arrival verified today", lambda r: "Yes, " + local_time(r["arrival_verified_at"], tz())
                   if r["arrival_verified_today"] else "No"),
            Column("Note", lambda r: r.get("note") or ""),
        ], key="doctor_id"))
        self.table.setAccessibleName("Doctor availability")
        self.stack = StateStack(self.table)
        self.btn_verify = QPushButton("&Verify arrival…")
        self.btn_verify.setToolTip("Confirm you have seen the doctor in the hospital today.")
        refresh = QPushButton("Refresh")
        top = QHBoxLayout()
        top.addWidget(QLabel("Statuses are reported by doctors in their app; arrival can be verified by "
                             "reception. Location tracking is not used.", wordWrap=True, objectName="subtle"), 1)
        top.addWidget(self.btn_verify)
        top.addWidget(refresh)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.stack, 1)
        self.btn_verify.clicked.connect(self._verify)
        refresh.clicked.connect(self.refresh)
        self.table.selectionModel().selectionChanged.connect(self._update_buttons)
        self._update_buttons()

    def is_relevant(self, event: dict) -> bool:
        return event.get("type") in ("doctor_status", "queue_entry")

    def refresh(self) -> None:
        if self.state.hospital is None:
            return
        self.stack.show_loading()
        hid = self.state.hospital_id

        def done(rows):
            self.table.set_rows_keep_selection(rows, key="doctor_id")
            if rows:
                self.stack.show_content()
            else:
                self.stack.show_empty("No doctors are registered at this hospital.")
            self._update_buttons()

        run_task(lambda: self.state.client.doctor_availability(hid), done,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    def _update_buttons(self) -> None:
        self.btn_verify.setEnabled(self.table.selected_record() is not None and self.state.can("availability.verify"))

    def _verify(self) -> None:
        doc = self.table.selected_record()
        if not doc:
            return
        dialog = ReasonDialog("Verify doctor arrival", f"Confirm that {doc['name']} is present in the hospital now. "
                              f"(Last update: {local(doc.get('status_updated_at'), self.state.hospital.timezone) or 'none'})",
                              "Verify arrival", required=False, parent=self)
        if dialog.exec():
            hid = self.state.hospital_id
            self.run_action(lambda: self.state.client.verify_arrival(hid, doc["doctor_id"], dialog.value()),
                            f"Arrival of {doc['name']} verified.")


def _badge(record: dict) -> str:
    # The internal "checked_in" status shares a label with appointments; colour it as a doctor status.
    return "checked_in_doctor" if record["status"] == "checked_in" else record["status"]
