from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from careflow_desktop.api.errors import ApiError
from careflow_desktop.formatting import local, status_label
from careflow_desktop.state import AppState
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.dialogs import DoctorChoiceDialog, DuplicateDialog, SlotPickerDialog
from careflow_desktop.ui.models import Column, RecordTableModel
from careflow_desktop.ui.widgets import RecordTable, StateStack, error_text, section_title

SEX_OPTIONS = [("Not recorded", None), ("Female", "female"), ("Male", "male"), ("Other", "other"),
               ("Prefers not to say", "undisclosed")]


def patient_columns() -> list[Column]:
    return [
        Column("Hospital no.", lambda r: r["hospital_patient_number"]),
        Column("Name", lambda r: r["full_name"]),
        Column("Phone", lambda r: r.get("phone") or ""),
        Column("Date of birth", lambda r: r.get("date_of_birth") or ""),
        Column("Online account", lambda r: "Yes" if r.get("has_online_account") else "No"),
    ]


class RegistrationForm(QWidget):
    """Minimal patient registration with server-side duplicate detection."""

    def __init__(self, state: AppState, on_registered: Callable[[dict], None], parent=None):
        super().__init__(parent)
        self.state = state
        self.on_registered = on_registered
        form = QFormLayout(self)
        self.name = QLineEdit(placeholderText="Full name as on hospital records")
        self.phone = QLineEdit(placeholderText="e.g. 0803 123 4567")
        self.dob_known = QCheckBox("Date of birth known")
        self.dob = QDateEdit(QDate(1990, 1, 1), calendarPopup=True, displayFormat="yyyy-MM-dd")
        self.dob.setMaximumDate(QDate.currentDate())
        self.dob.setEnabled(False)
        self.sex = QComboBox()
        for label, value in SEX_OPTIONS:
            self.sex.addItem(label, value)
        self.error = QLabel(objectName="errorText", wordWrap=True)
        self.error.hide()
        self.submit = QPushButton("Re&gister patient")
        form.addRow("Full name *", self.name)
        form.addRow("Phone", self.phone)
        form.addRow("", self.dob_known)
        form.addRow("Date of birth", self.dob)
        form.addRow("Sex", self.sex)
        form.addRow(self.error)
        form.addRow(self.submit)
        form.addRow(QLabel("Collect only what is needed to identify the patient. Do not record clinical "
                           "information here.", wordWrap=True, objectName="subtle"))
        self.dob_known.toggled.connect(self.dob.setEnabled)
        self.submit.clicked.connect(lambda: self._submit())
        self.name.returnPressed.connect(lambda: self._submit())

    def clear(self) -> None:
        self.name.clear()
        self.phone.clear()
        self.dob_known.setChecked(False)
        self.sex.setCurrentIndex(0)
        self.error.hide()

    def _payload(self) -> dict | None:
        name = " ".join(self.name.text().split())
        if len(name) < 2:
            self.error.setText("Enter the patient's full name.")
            self.error.show()
            return None
        return {"full_name": name, "phone": self.phone.text().strip() or None,
                "date_of_birth": self.dob.date().toPython().isoformat() if self.dob_known.isChecked() else None,
                "sex": self.sex.currentData()}

    def _submit(self, confirm_reason: str | None = None) -> None:
        payload = self._payload()
        if payload is None:
            return
        if confirm_reason:
            payload.update(confirm_not_duplicate=True, duplicate_override_reason=confirm_reason)
        self.error.hide()
        self.submit.setEnabled(False)
        hid = self.state.hospital_id

        def done(patient):
            self.submit.setEnabled(True)
            self.clear()
            self.state.status_message.emit(
                f"Registered {patient['full_name']} as {patient['hospital_patient_number']}.")
            self.on_registered(patient)

        def failed(exc):
            self.submit.setEnabled(True)
            if isinstance(exc, ApiError) and exc.code == "possible_duplicate":
                dialog = DuplicateDialog(exc.details["candidates"], parent=self)
                result = dialog.exec()
                if result == DuplicateDialog.USE_EXISTING and dialog.selected():
                    c = dialog.selected()
                    self.clear()
                    self.on_registered({"id": c["patient_id"], "full_name": c["full_name"],
                                        "hospital_patient_number": c["hospital_patient_number"],
                                        "date_of_birth": c.get("date_of_birth")})
                elif result == DuplicateDialog.REGISTER_ANYWAY:
                    self._submit(confirm_reason=dialog.reason.text().strip())
                return
            self.error.setText(error_text(exc))
            self.error.show()

        run_task(lambda: self.state.client.register_patient(hid, **payload), done, failed, owner=self)


class _PatientSearch(QWidget):
    """Search box + results table. Searches by hospital number, phone or name (min. 3 characters)."""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.query = QLineEdit(placeholderText="Search by hospital number, phone or name  (Ctrl+F)")
        self.query.setClearButtonEnabled(True)
        self.table = RecordTable(RecordTableModel(patient_columns()))
        self.table.setAccessibleName("Patient search results")
        self.stack = StateStack(self.table)
        self.stack.show_empty("Type at least 3 characters to search.")
        layout.addWidget(self.query)
        layout.addWidget(self.stack, 1)
        self._timer = QTimer(self, singleShot=True, interval=350)
        self._timer.timeout.connect(self.search)
        self.query.textChanged.connect(lambda _: self._timer.start())
        self.query.returnPressed.connect(self.search)

    def search(self) -> None:
        q = self.query.text().strip()
        if len(q) < 3:
            self.table.record_model.set_rows([])
            self.stack.show_empty("Type at least 3 characters to search.")
            return
        self.stack.show_loading()
        hid = self.state.hospital_id

        def done(rows):
            if self.query.text().strip() != q:
                return  # a newer search is in progress
            self.table.set_rows_keep_selection(rows)
            if rows:
                self.stack.show_content()
                self.table.selectRow(0)
            else:
                self.stack.show_empty(f'No patients found for "{q}".')

        run_task(lambda: self.state.client.search_patients(hid, q), done,
                 lambda exc: self.stack.show_error(error_text(exc), self.search), owner=self)


class PatientPickerDialog(QDialog):
    """Find an existing patient or register a new one; `patient` holds the choice."""

    def __init__(self, state: AppState, title: str, ok_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        self.patient: dict | None = None
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(section_title("Find patient"))
        self.search = _PatientSearch(state)
        left.addWidget(self.search, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(ok_label)
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        left.addWidget(buttons)
        layout.addLayout(left, 3)
        box = QGroupBox("Or register a new patient")
        bl = QVBoxLayout(box)
        bl.addWidget(RegistrationForm(state, self._registered))
        layout.addWidget(box, 2)
        self.search.table.doubleClicked.connect(lambda _: self._accept_selected())
        self.search.query.setFocus()

    def _accept_selected(self) -> None:
        record = self.search.table.selected_record()
        if record:
            self.patient = record
            self.accept()

    def _registered(self, patient: dict) -> None:
        self.patient = patient
        self.accept()


class PatientsView(BaseView):
    title = "Patients"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self.search = _PatientSearch(state)

        details = QWidget()
        dl = QVBoxLayout(details)
        dl.addWidget(section_title("Selected patient"))
        self.selected = QLabel("Select a patient to see their appointments.", wordWrap=True)
        dl.addWidget(self.selected)
        actions = QHBoxLayout()
        self.btn_book = QPushButton("&Book appointment…")
        self.btn_queue = QPushButton("Add to today's &queue…")
        actions.addWidget(self.btn_book)
        actions.addWidget(self.btn_queue)
        actions.addStretch()
        dl.addLayout(actions)
        dl.addWidget(section_title("Appointments"))
        self.appointments = QListWidget()
        self.appointments.setAccessibleName("Patient appointments")
        dl.addWidget(self.appointments, 1)

        left = QSplitter(Qt.Vertical)
        left.addWidget(self.search)
        left.addWidget(details)

        register_box = QGroupBox("Register new patient")
        rl = QVBoxLayout(register_box)
        self.form = RegistrationForm(state, self._registered)
        rl.addWidget(self.form)
        rl.addStretch()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(register_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self.search.table.selectionModel().selectionChanged.connect(self._show_selected)
        self.btn_book.clicked.connect(self._book)
        self.btn_queue.clicked.connect(self._add_to_queue)
        QShortcut(QKeySequence.Find, self, activated=self.search.query.setFocus)
        self._show_selected()

    def reset(self) -> None:
        self.search.query.clear()
        self.search.table.record_model.set_rows([])

    def refresh(self) -> None:
        if self.search.query.text().strip():
            self.search.search()
        self._show_selected()

    def is_relevant(self, event: dict) -> bool:
        return False  # patient records do not change through queue events

    def _registered(self, patient: dict) -> None:
        self.search.query.setText(patient["hospital_patient_number"])
        self.search.search()

    def _patient(self) -> dict | None:
        return self.search.table.selected_record()

    def _show_selected(self) -> None:
        p = self._patient()
        self.btn_book.setEnabled(p is not None and self.state.can("appointments.review"))
        self.btn_queue.setEnabled(p is not None and self.state.can("queue.manage"))
        self.appointments.clear()
        if p is None:
            self.selected.setText("Select a patient to see their appointments.")
            return
        self.selected.setText(f"<b>{p['full_name']}</b> — {p['hospital_patient_number']}"
                              + (f"<br>Phone: {p['phone']}" if p.get("phone") else ""))
        hid, pid, tz = self.state.hospital_id, p["id"], self.state.hospital.timezone

        def done(page):
            current = self._patient()
            if not current or current["id"] != pid:
                return
            self.appointments.clear()
            for a in page["items"]:
                self.appointments.addItem(f"{local(a['start_at'], tz)} — {a['doctor_name']} — "
                                          f"{status_label(a['status'])}")
            if not page["items"]:
                self.appointments.addItem("No appointments at this hospital.")

        run_task(lambda: self.state.client.appointments(hid, patient_id=pid, limit=50), done,
                 lambda exc: self.appointments.addItem(error_text(exc)), owner=self)

    def _book(self) -> None:
        p = self._patient()
        if not p:
            return
        choose = DoctorChoiceDialog(self.state.doctors, "Book appointment", f"Book an appointment for "
                                    f"{p['full_name']}. Staff bookings are approved immediately.", "Next",
                                    ask_reason=False, parent=self)
        if not choose.exec():
            return
        doctor_id = choose.doctor_id()
        slot = SlotPickerDialog(self.state, doctor_id, "Choose a time",
                                f"{self.state.doctor_name(doctor_id)} — available times", "Book appointment",
                                ask_reason=False, parent=self)
        if slot.exec():
            hid = self.state.hospital_id
            self.run_action(lambda: self.state.client.book_appointment(
                hid, patient_id=p["id"], doctor_id=doctor_id, start_at=slot.selected_start(), reason_for_visit=None),
                "Appointment booked.")

    def _add_to_queue(self) -> None:
        p = self._patient()
        if not p:
            return
        dialog = DoctorChoiceDialog(self.state.doctors, "Add walk-in to queue",
                                    f"Add {p['full_name']} to a doctor's queue today.", "Add to queue",
                                    ask_reason=False, parent=self)
        if dialog.exec():
            hid, doctor_id = self.state.hospital_id, dialog.doctor_id()
            self.run_action(lambda: self.state.client.add_walk_in(hid, patient_id=p["id"], doctor_id=doctor_id),
                            f"{p['full_name']} added to {self.state.doctor_name(doctor_id)}'s queue.")

