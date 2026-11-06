from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from careflow_desktop.formatting import local
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.models import Column, RecordTableModel
from careflow_desktop.ui.widgets import RecordTable, StateStack, error_text, show_error

TARGET_TYPES = [("All records", None), ("Appointments", "appointment"), ("Queue entries", "queue_entry"),
                ("Queues", "queue"), ("Patients", "patient"), ("Doctors", "doctor"), ("Staff", "user"),
                ("Schedules", "schedule_template")]
PAGE_SIZE = 100


class AuditView(BaseView):
    title = "Audit & Reports"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._cursor: str | None = None
        self.target = QComboBox()
        for label, value in TARGET_TYPES:
            self.target.addItem(label, value)
        self.action = QLineEdit(placeholderText="Exact action, e.g. appointment.approved")
        self.action.setClearButtonEnabled(True)
        self.date_from = QDateEdit(QDate.currentDate().addDays(-7), calendarPopup=True, displayFormat="yyyy-MM-dd")
        self.date_to = QDateEdit(QDate.currentDate(), calendarPopup=True, displayFormat="yyyy-MM-dd")
        apply = QPushButton("Apply filters")
        filters = QHBoxLayout()
        for label, widget in (("Record", self.target), ("Action", self.action), ("From", self.date_from),
                              ("To", self.date_to)):
            lbl = QLabel(label)
            lbl.setBuddy(widget)
            filters.addWidget(lbl)
            filters.addWidget(widget)
        filters.addWidget(apply)

        exports = QHBoxLayout()
        self.report_date = QDateEdit(QDate.currentDate(), calendarPopup=True, displayFormat="yyyy-MM-dd")
        self.btn_queue_report = QPushButton("Export queue report (CSV)…")
        self.btn_audit_export = QPushButton("Export audit log (CSV)…")
        exports.addWidget(QLabel("Queue report for"))
        exports.addWidget(self.report_date)
        exports.addWidget(self.btn_queue_report)
        exports.addStretch()
        exports.addWidget(self.btn_audit_export)

        tz = lambda: self.state.hospital.timezone if self.state.hospital else "UTC"  # noqa: E731
        self.table = RecordTable(RecordTableModel([
            Column("Time", lambda r: local(r["occurred_at"], tz(), "%Y-%m-%d %H:%M:%S"), sort=lambda r: r["occurred_at"]),
            Column("Action", lambda r: r["action"]),
            Column("By", lambda r: r.get("actor_roles") or ("system" if not r.get("actor_user_id") else "")),
            Column("Record", lambda r: f"{r.get('target_type') or ''} {(r.get('target_id') or '')[:8]}".strip()),
            Column("Reason", lambda r: r.get("reason") or ""),
            Column("Details", lambda r: ", ".join(f"{k}={v}" for k, v in (r.get("details") or {}).items())),
        ]))
        self.table.setAccessibleName("Audit events")
        self.stack = StateStack(self.table)
        self.load_more = QPushButton("Load more")
        self.load_more.hide()

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.load_more)
        layout.addLayout(exports)

        apply.clicked.connect(self.refresh)
        self.action.returnPressed.connect(self.refresh)
        self.load_more.clicked.connect(self._more)
        self.btn_queue_report.clicked.connect(self._export_queue)
        self.btn_audit_export.clicked.connect(self._export_audit)

    def is_relevant(self, event: dict) -> bool:
        return False  # audit history is loaded on demand

    def _range(self) -> tuple[str, str]:
        tz = ZoneInfo(self.state.hospital.timezone)
        start = datetime.combine(self.date_from.date().toPython(), time.min, tzinfo=tz)
        end = datetime.combine(self.date_to.date().toPython() + timedelta(days=1), time.min, tzinfo=tz)
        return start.isoformat(), end.isoformat()

    def _filters(self) -> dict:
        start, end = self._range()
        return {"limit": PAGE_SIZE, "target_type": self.target.currentData(),
                "action": self.action.text().strip() or None, "occurred_from": start, "occurred_to": end}

    def refresh(self) -> None:
        if self.state.hospital is None:
            return
        can_view = self.state.can("audit.view")
        can_export = self.state.can("audit.export")
        self.btn_queue_report.setEnabled(can_export)
        self.btn_audit_export.setEnabled(can_export and can_view)
        if not can_view:
            self.stack.show_empty("You do not have permission to view the audit history.")
            return
        self.stack.show_loading()
        hid, filters = self.state.hospital_id, self._filters()

        def done(page):
            self._cursor = page["next_cursor"]
            self.table.set_rows_keep_selection(page["items"])
            self.load_more.setVisible(bool(self._cursor))
            if page["items"]:
                self.stack.show_content()
            else:
                self.stack.show_empty("No audit events match these filters.")

        run_task(lambda: self.state.client.audit_events(hid, **filters), done,
                 lambda exc: self.stack.show_error(error_text(exc), self.refresh), owner=self)

    def _more(self) -> None:
        hid, filters = self.state.hospital_id, {**self._filters(), "cursor": self._cursor}

        def done(page):
            self._cursor = page["next_cursor"]
            self.table.record_model.append_rows(page["items"])
            self.load_more.setVisible(bool(self._cursor))

        run_task(lambda: self.state.client.audit_events(hid, **filters), done,
                 lambda exc: show_error(self, exc), owner=self)

    def _save(self, default_name: str, fetch) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save report", str(Path.home() / default_name), "CSV files (*.csv)")
        if not path:
            return

        def done(content: bytes):
            Path(path).write_bytes(content)
            self.notify(f"Saved {Path(path).name}. It may contain personal data: store it securely.")

        run_task(fetch, done, lambda exc: show_error(self, exc, "Export failed"), owner=self)

    def _export_queue(self) -> None:
        day = self.report_date.date().toPython().isoformat()
        hid = self.state.hospital_id
        self._save(f"careflow-queue-{day}.csv", lambda: self.state.client.export_queue_day_csv(hid, day))

    def _export_audit(self) -> None:
        hid = self.state.hospital_id
        start, end = self._range()
        filters = {"occurred_from": start, "occurred_to": end, "target_type": self.target.currentData(),
                   "action": self.action.text().strip() or None}
        self._save("careflow-audit.csv", lambda: self.state.client.export_audit_csv(hid, **filters))
