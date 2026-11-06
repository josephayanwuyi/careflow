from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from careflow_desktop.formatting import event_label, local_time, status_label
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.models import Column, RecordTableModel
from careflow_desktop.ui.widgets import RecordTable, StatCard, StateStack, error_text, section_title


class DashboardView(BaseView):
    title = "Dashboard"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        content = QWidget()
        layout = QVBoxLayout(content)

        self.date_label = QLabel(objectName="subtle")
        layout.addWidget(self.date_label)

        grid = QGridLayout()
        self.cards = {
            "waiting": StatCard("Patients waiting"),
            "with_doctor": StatCard("With a doctor (called or in consultation)"),
            "completed": StatCard("Consultations completed today"),
            "pending": StatCard("Appointment requests awaiting approval"),
            "awaiting": StatCard("Proposed new times awaiting patient"),
            "today": StatCard("Appointments today"),
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, i // 3, i % 3)
        layout.addLayout(grid)

        row = QHBoxLayout()
        doctors_box = QVBoxLayout()
        doctors_box.addWidget(section_title("Doctors (declared availability)"))
        self.doctors_label = QLabel(wordWrap=True)
        doctors_box.addWidget(self.doctors_label)
        doctors_box.addStretch()
        row.addLayout(doctors_box, 1)

        alerts_box = QVBoxLayout()
        alerts_box.addWidget(section_title("Queue alerts today"))
        self.alerts = RecordTable(RecordTableModel([
            Column("Time", lambda r: local_time(r["occurred_at"], self.state.hospital.timezone if self.state.hospital else "UTC")),
            Column("Event", lambda r: event_label(r["event_type"])),
            Column("Ticket", lambda r: r.get("ticket_number") or ""),
            Column("Doctor", lambda r: r.get("doctor_name") or ""),
            Column("Reason", lambda r: r.get("reason") or ""),
        ]), sortable=False)
        alerts_box.addWidget(self.alerts)
        row.addLayout(alerts_box, 2)
        layout.addLayout(row)

        self.stack = StateStack(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)

    def is_relevant(self, event: dict) -> bool:
        return True  # any change can affect the counts

    def refresh(self) -> None:
        if self.state.hospital is None:
            return
        self.stack.show_loading()
        hid = self.state.hospital_id
        run_task(lambda: self.state.client.dashboard(hid), self._show,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    def _show(self, data: dict) -> None:
        q, a = data["queue"], data["appointments"]
        self.date_label.setText(f"Today: {data['date']} ({data['timezone']})")
        self.cards["waiting"].set_value(q["waiting"])
        self.cards["with_doctor"].set_value(q["called"] + q["in_consultation"])
        self.cards["completed"].set_value(q["completed"])
        self.cards["pending"].set_value(a["pending_approval"])
        self.cards["awaiting"].set_value(a["awaiting_patient_response"])
        self.cards["today"].set_value(a["today"])
        doctors = data["doctors"]
        self.doctors_label.setText("\n".join(f"{status_label(k)}: {v}" for k, v in sorted(doctors.items()))
                                   or "No doctors registered.")
        self.alerts.set_rows_keep_selection(data["alerts"], key="occurred_at")
        self.stack.show_content()
