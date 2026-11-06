"""Dialogs for consequential actions. All of them only collect input; the server validates it."""

from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from careflow_desktop.formatting import local_time
from careflow_desktop.state import AppState
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.widgets import error_text


class _FormDialog(QDialog):
    def __init__(self, title: str, intro: str | None, ok_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.layout_ = QVBoxLayout(self)
        if intro:
            label = QLabel(intro, wordWrap=True)
            self.layout_.addWidget(label)
        self.form = QFormLayout()
        self.layout_.addLayout(self.form)
        self.error = QLabel(objectName="errorText", wordWrap=True)
        self.error.hide()
        self.layout_.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(ok_label)
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        self.layout_.addWidget(self.buttons)

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def validate(self) -> str | None:
        return None

    def _try_accept(self) -> None:
        problem = self.validate()
        if problem:
            self.show_error(problem)
            return
        self.accept()


class ReasonDialog(_FormDialog):
    """Ask for a reason (required or optional) before a consequential action."""

    def __init__(self, title: str, intro: str, ok_label: str, required: bool = True, parent=None):
        super().__init__(title, intro, ok_label, parent)
        self.required = required
        self.reason = QPlainTextEdit()
        self.reason.setPlaceholderText("Reason (required)" if required else "Reason (optional)")
        self.reason.setFixedHeight(90)
        self.reason.setTabChangesFocus(True)
        self.form.addRow("Reason", self.reason)

    def validate(self) -> str | None:
        if self.required and len(self.reason.toPlainText().strip()) < 3:
            return "Please enter a reason (at least 3 characters)."
        return None

    def value(self) -> str | None:
        return self.reason.toPlainText().strip() or None


class SlotPickerDialog(_FormDialog):
    """Choose a date and an available slot for a doctor (used for rescheduling and booking)."""

    def __init__(self, state: AppState, doctor_id: str, title: str, intro: str, ok_label: str,
                 ask_reason: bool, parent=None):
        super().__init__(title, intro, ok_label, parent)
        self.state = state
        self.doctor_id = doctor_id
        self.date = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.date.setMinimumDate(QDate.currentDate())
        self.slots = QListWidget()
        self.slots.setMinimumHeight(180)
        self.slot_status = QLabel()
        self.form.addRow("Date", self.date)
        self.form.addRow("Available times", self.slots)
        self.form.addRow("", self.slot_status)
        self.reason: QPlainTextEdit | None = None
        if ask_reason:
            self.reason = QPlainTextEdit()
            self.reason.setFixedHeight(70)
            self.reason.setTabChangesFocus(True)
            self.form.addRow("Reason", self.reason)
        self.date.dateChanged.connect(self.load_slots)
        self.slots.itemDoubleClicked.connect(lambda _: self._try_accept())
        self.load_slots()

    def load_slots(self) -> None:
        day = self.date.date().toPython().isoformat()
        self.slots.clear()
        self.slot_status.setText("Loading times…")

        def done(data):
            self.slots.clear()
            available = [s for s in data["slots"] if s["available"]]
            for s in available:
                item = QListWidgetItem(local_time(s["start_at"], data["timezone"]))
                item.setData(Qt.UserRole, s["start_at"])
                self.slots.addItem(item)
            self.slot_status.setText("" if available else "No available times on this date.")
            if available:
                self.slots.setCurrentRow(0)

        run_task(lambda: self.state.client.slots(self.doctor_id, day), done,
                 lambda exc: self.slot_status.setText(error_text(exc)), owner=self)

    def validate(self) -> str | None:
        if self.slots.currentItem() is None:
            return "Choose an available time."
        if self.reason is not None and len(self.reason.toPlainText().strip()) < 3:
            return "Please enter a reason (at least 3 characters)."
        return None

    def selected_start(self) -> str:
        return self.slots.currentItem().data(Qt.UserRole)

    def reason_text(self) -> str | None:
        return self.reason.toPlainText().strip() if self.reason else None


class DoctorChoiceDialog(_FormDialog):
    def __init__(self, doctors: list[dict], title: str, intro: str, ok_label: str, exclude: str | None = None,
                 ask_reason: bool = True, parent=None):
        super().__init__(title, intro, ok_label, parent)
        self.doctor = QComboBox()
        for d in doctors:
            if d["id"] != exclude:
                self.doctor.addItem(f"{d['display_name']} — {d['department_name']}", d["id"])
        self.form.addRow("Doctor", self.doctor)
        self.reason: QPlainTextEdit | None = None
        if ask_reason:
            self.reason = QPlainTextEdit()
            self.reason.setFixedHeight(70)
            self.reason.setTabChangesFocus(True)
            self.form.addRow("Reason", self.reason)

    def validate(self) -> str | None:
        if self.doctor.currentData() is None:
            return "Choose a doctor."
        if self.reason is not None and len(self.reason.toPlainText().strip()) < 3:
            return "Please enter a reason (at least 3 characters)."
        return None

    def doctor_id(self) -> str:
        return self.doctor.currentData()

    def reason_text(self) -> str | None:
        return self.reason.toPlainText().strip() if self.reason else None


class PriorityDialog(_FormDialog):
    def __init__(self, current: int, parent=None):
        super().__init__("Change queue priority",
                         "Priority must follow the hospital's clinical protocols. The software does not "
                         "decide priority; record who authorised it and why.", "Set priority", parent)
        self.level = QSpinBox(minimum=0, maximum=3, value=current)
        self.level.setToolTip("0 = normal, 3 = highest")
        self.reason = QPlainTextEdit()
        self.reason.setFixedHeight(80)
        self.reason.setTabChangesFocus(True)
        self.form.addRow("Level (0–3)", self.level)
        self.form.addRow("Reason", self.reason)

    def validate(self) -> str | None:
        if len(self.reason.toPlainText().strip()) < 3:
            return "Please enter a reason (at least 3 characters)."
        return None


class DuplicateDialog(QDialog):
    """Shown when registration matches existing patients. Staff can pick one or register anyway with a reason."""

    USE_EXISTING, REGISTER_ANYWAY = 1, 2

    def __init__(self, candidates: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Possible duplicate patient")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("A patient with matching details is already registered at this hospital. "
                                "Check whether this is the same person before creating a new record.",
                                wordWrap=True))
        self.list = QListWidget()
        for c in candidates:
            dob = c.get("date_of_birth") or "DOB not recorded"
            phone = f"phone ending {c['phone_last4']}" if c.get("phone_last4") else "no phone"
            item = QListWidgetItem(f"{c['hospital_patient_number']} — {c['full_name']} ({dob}, {phone})")
            item.setData(Qt.UserRole, c)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        layout.addWidget(self.list)
        layout.addWidget(QLabel("If this is a different person, give a reason to register them anyway:"))
        self.reason = QLineEdit(placeholderText="e.g. Different person sharing a family phone")
        layout.addWidget(self.reason)
        self.error = QLabel(objectName="errorText")
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        use = QPushButton("Use selected patient")
        use.setDefault(True)
        anyway = QPushButton("Register as new patient")
        cancel = QPushButton("Cancel")
        buttons.addWidget(use)
        buttons.addWidget(anyway)
        buttons.addStretch()
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        use.clicked.connect(lambda: self.done(self.USE_EXISTING))
        anyway.clicked.connect(self._anyway)
        cancel.clicked.connect(self.reject)

    def _anyway(self) -> None:
        if len(self.reason.text().strip()) < 3:
            self.error.setText("A reason is required to register a possible duplicate.")
            self.error.show()
            return
        self.done(self.REGISTER_ANYWAY)

    def selected(self) -> dict | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

