from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from careflow_desktop.tasks import run_task
from careflow_desktop.ui.base import BaseView
from careflow_desktop.ui.widgets import StatCard, show_error


class AddStaffDialog(QDialog):
    """Dialog allowing hospital administrators to create receptionists, doctors, or admin staff."""

    def __init__(self, parent=None, departments: list[dict] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Staff Member / Receptionist")
        self.setMinimumWidth(440)
        self.departments = departments or []

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Add a receptionist, doctor, or staff member directly without waiting for IT setup. "
            "They will be able to sign in immediately using their email and temporary password."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #475569; margin-bottom: 8px;")
        layout.addWidget(intro)

        form = QFormLayout()

        self.full_name = QLineEdit(placeholderText="e.g. Sarah Jenkins")
        form.addRow("Full Name *", self.full_name)

        self.email = QLineEdit(placeholderText="e.g. sarah.jenkins@hospital.org")
        form.addRow("Email Address *", self.email)

        self.password = QLineEdit(placeholderText="At least 8 characters")
        self.password.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        form.addRow("Temporary Password *", self.password)

        self.role_combo = QComboBox()
        self.role_combo.addItem("Receptionist (Front Desk / Check-in)", "receptionist")
        self.role_combo.addItem("Doctor (Consultation & Clinical Queue)", "doctor")
        self.role_combo.addItem("Hospital Admin (Facility & Staff Management)", "hospital_admin")
        form.addRow("Role *", self.role_combo)

        self.department_combo = QComboBox()
        self.department_combo.addItem("(None / General)", None)
        for d in self.departments:
            self.department_combo.addItem(d.get("name", "Department"), str(d.get("id")))
        form.addRow("Department", self.department_combo)

        self.phone = QLineEdit(placeholderText="e.g. +1 555-0192")
        form.addRow("Phone Number", self.phone)

        self.job_title = QLineEdit(placeholderText="e.g. Lead Receptionist / Dr.")
        form.addRow("Title / Designation", self.job_title)

        self.specialty = QLineEdit(placeholderText="e.g. Pediatrics (Doctor only)")
        form.addRow("Specialty", self.specialty)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Add Staff Member")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        name = self.full_name.text().strip()
        email = self.email.text().strip()
        pwd = self.password.text().strip()

        if not name:
            QMessageBox.warning(self, "Required Field", "Please enter the staff member's full name.")
            self.full_name.setFocus()
            return

        if not email or "@" not in email:
            QMessageBox.warning(self, "Invalid Email", "Please enter a valid email address.")
            self.email.setFocus()
            return

        if len(pwd) < 8:
            QMessageBox.warning(self, "Password Too Short", "Temporary password must be at least 8 characters.")
            self.password.setFocus()
            return

        self.accept()

    def value(self) -> dict:
        role = self.role_combo.currentData()
        dept_id = self.department_combo.currentData()
        data = {
            "full_name": self.full_name.text().strip(),
            "email": self.email.text().strip(),
            "temporary_password": self.password.text().strip(),
            "roles": [role],
            "phone": self.phone.text().strip() or None,
            "title": self.job_title.text().strip() or None,
        }
        if dept_id:
            data["department_id"] = dept_id
        if role == "doctor" and self.specialty.text().strip():
            data["specialty"] = self.specialty.text().strip()
        return data


class AddDepartmentDialog(QDialog):
    """Dialog allowing hospital administrators to add new clinic departments."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Hospital Department")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit(placeholderText="e.g. Pediatrics, Cardiology, Emergency")
        form.addRow("Department Name *", self.name_edit)

        self.desc_edit = QLineEdit(placeholderText="e.g. Children and outpatient care")
        form.addRow("Description", self.desc_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create Department")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        name = self.name_edit.text().strip()
        if len(name) < 2:
            QMessageBox.warning(self, "Invalid Name", "Department name must be at least 2 characters.")
            self.name_edit.setFocus()
            return
        self.accept()

    def value(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "description": self.desc_edit.text().strip() or None,
        }


class AdminView(BaseView):
    title = "Admin & Staff"

    def __init__(self, state, parent=None):
        super().__init__(state, parent)
        self._departments: list[dict] = []
        self._staff: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 1. Facility Header Overview
        info_group = QGroupBox("Facility & Organization Administration")
        info_layout = QHBoxLayout(info_group)
        self.lbl_org_name = QLabel("Loading facility details…")
        self.lbl_org_name.setStyleSheet("font-weight: bold; font-size: 14px; color: #0f172a;")
        self.lbl_code = QLabel("")
        self.lbl_code.setStyleSheet("color: #0b4f8a; font-weight: bold;")
        self.lbl_tz = QLabel("")
        self.lbl_address = QLabel("")

        info_layout.addWidget(self.lbl_org_name)
        info_layout.addWidget(self.lbl_code)
        info_layout.addStretch()
        info_layout.addWidget(self.lbl_address)
        info_layout.addWidget(self.lbl_tz)

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh)
        info_layout.addWidget(btn_refresh)
        layout.addWidget(info_group)

        # 2. Main Tabs: Staff, Departments, Notification Reports
        self.tabs = QTabWidget()

        # Tab 1: Staff & Receptionists
        staff_tab = QWidget()
        staff_layout = QVBoxLayout(staff_tab)
        staff_bar = QHBoxLayout()
        staff_label = QLabel("Active Staff Members & Receptionists")
        staff_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        staff_bar.addWidget(staff_label)
        staff_bar.addStretch()
        self.btn_add_staff = QPushButton("➕ Add Staff / Receptionist")
        self.btn_add_staff.setStyleSheet("font-weight: bold; padding: 6px 14px;")
        self.btn_add_staff.clicked.connect(self._on_add_staff_clicked)
        staff_bar.addWidget(self.btn_add_staff)
        staff_layout.addLayout(staff_bar)

        self.staff_table = QTableWidget()
        self.staff_table.setColumnCount(6)
        self.staff_table.setHorizontalHeaderLabels(["Full Name", "Email", "Roles", "Department", "Phone", "Title"])
        self.staff_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.staff_table.setAlternatingRowColors(True)
        staff_layout.addWidget(self.staff_table)
        self.tabs.addTab(staff_tab, "👥 Staff & Receptionists")

        # Tab 2: Departments
        dept_tab = QWidget()
        dept_layout = QVBoxLayout(dept_tab)
        dept_bar = QHBoxLayout()
        dept_label = QLabel("Clinical Departments & Service Areas")
        dept_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        dept_bar.addWidget(dept_label)
        dept_bar.addStretch()
        self.btn_add_dept = QPushButton("➕ Add Department")
        self.btn_add_dept.setStyleSheet("font-weight: bold; padding: 6px 14px;")
        self.btn_add_dept.clicked.connect(self._on_add_dept_clicked)
        dept_bar.addWidget(self.btn_add_dept)
        dept_layout.addLayout(dept_bar)

        self.dept_table = QTableWidget()
        self.dept_table.setColumnCount(2)
        self.dept_table.setHorizontalHeaderLabels(["Department Name", "Description"])
        self.dept_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.dept_table.setAlternatingRowColors(True)
        dept_layout.addWidget(self.dept_table)
        self.tabs.addTab(dept_tab, "🏢 Departments")

        # Tab 3: Notification Delivery Reports
        notif_tab = QWidget()
        notif_layout = QVBoxLayout(notif_tab)

        banner = QLabel(
            "✨ Self-Hosted Notifications: Zero token charges. "
            "SMS is dispatched on initial patient queue calls; "
            "Email and in-app push notifications are dispatched on initial calls and all recalls."
        )
        banner.setStyleSheet(
            "background-color: #f0fdf4; border: 1px solid #bbf7d0; "
            "color: #166534; padding: 8px 12px; border-radius: 6px; font-weight: 500;"
        )
        notif_layout.addWidget(banner)

        cards_layout = QHBoxLayout()
        self.card_sms = StatCard("SMS Dispatched\n(Initial Call)")
        self.card_email = StatCard("Emails Dispatched\n(Initial Call & Recalls)")
        self.card_push = StatCard("Push Alerts Dispatched\n(Real-Time In-App)")
        cards_layout.addWidget(self.card_sms)
        cards_layout.addWidget(self.card_email)
        cards_layout.addWidget(self.card_push)
        notif_layout.addLayout(cards_layout)

        audit_label = QLabel("Recent Notification Audit Trail (Last 50 Dispatches)")
        audit_label.setStyleSheet("font-weight: bold; font-size: 12px; margin-top: 8px;")
        notif_layout.addWidget(audit_label)

        self.notif_table = QTableWidget()
        self.notif_table.setColumnCount(5)
        self.notif_table.setHorizontalHeaderLabels(["Timestamp", "Channel", "Type", "Title / Subject", "Message Preview"])
        self.notif_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.notif_table.setAlternatingRowColors(True)
        notif_layout.addWidget(self.notif_table)
        self.tabs.addTab(notif_tab, "📊 Notification Reports")

        layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        if not self.state.hospital:
            return
        hid = self.state.hospital.id
        self.lbl_org_name.setText(self.state.hospital.name)
        code = getattr(self.state.hospital, "code", "MAIN")
        self.lbl_code.setText(f"Code: {code}")
        self.lbl_tz.setText(f"Timezone: {self.state.hospital.timezone}")
        addr = getattr(self.state.hospital, "address", "") or ""
        self.lbl_address.setText(f"Address: {addr}" if addr else "")

        def _fetch():
            client = self.state.client
            staff = client.list_staff(hid) if hasattr(client, "list_staff") else []
            depts = client.departments(hid) if hasattr(client, "departments") else []
            report = client.notification_report(hid) if hasattr(client, "notification_report") else {
                "sms_sent": 0, "email_sent": 0, "push_sent": 0, "items": []
            }
            return staff, depts, report

        run_task(
            _fetch,
            on_success=self._on_data_loaded,
            on_error=lambda exc: show_error(self, exc, title="Cannot Load Admin Data"),
            owner=self,
        )

    def _on_data_loaded(self, data: tuple) -> None:
        staff, depts, report = data
        self._departments = depts
        self._staff = staff

        # 1. Populate Staff Table
        self.staff_table.setRowCount(len(staff))
        for row, s in enumerate(staff):
            name = s.get("full_name", "")
            email = s.get("email", "")
            roles = ", ".join(s.get("roles", []))
            dept = s.get("department_name") or "–"
            phone = s.get("phone") or "–"
            title = s.get("title") or "–"

            self.staff_table.setItem(row, 0, QTableWidgetItem(name))
            self.staff_table.setItem(row, 1, QTableWidgetItem(email))
            self.staff_table.setItem(row, 2, QTableWidgetItem(roles))
            self.staff_table.setItem(row, 3, QTableWidgetItem(dept))
            self.staff_table.setItem(row, 4, QTableWidgetItem(phone))
            self.staff_table.setItem(row, 5, QTableWidgetItem(title))

        # 2. Populate Department Table
        self.dept_table.setRowCount(len(depts))
        for row, d in enumerate(depts):
            dname = d.get("name", "")
            desc = d.get("description") or "–"
            self.dept_table.setItem(row, 0, QTableWidgetItem(dname))
            self.dept_table.setItem(row, 1, QTableWidgetItem(desc))

        # 3. Populate Notification Reports & Log Table
        sms_sent = report.get("sms_sent", 0)
        email_sent = report.get("email_sent", 0)
        push_sent = report.get("push_sent", 0)

        self.card_sms.set_value(sms_sent)
        self.card_email.set_value(email_sent)
        self.card_push.set_value(push_sent)

        items = report.get("items", [])
        self.notif_table.setRowCount(len(items))
        for row, item in enumerate(items):
            ts = item.get("timestamp", "")[:19].replace("T", " ")
            channel = item.get("channel", "sms,email,push").upper()
            ntype = item.get("type", "").replace("_", " ").title()
            title = item.get("title", "")
            body = item.get("body", "")

            self.notif_table.setItem(row, 0, QTableWidgetItem(ts))
            self.notif_table.setItem(row, 1, QTableWidgetItem(channel))
            self.notif_table.setItem(row, 2, QTableWidgetItem(ntype))
            self.notif_table.setItem(row, 3, QTableWidgetItem(title))
            self.notif_table.setItem(row, 4, QTableWidgetItem(body))

    def _on_add_staff_clicked(self) -> None:
        if not self.state.hospital:
            return
        dlg = AddStaffDialog(self, departments=self._departments)
        if dlg.exec() != QDialog.Accepted:
            return

        payload = dlg.value()
        hid = self.state.hospital.id

        def _do_create():
            return self.state.client.create_staff(hid, **payload)

        self.run_action(
            _do_create,
            success_message=f"Staff member '{payload['full_name']}' created successfully.",
            on_success=lambda _: self.refresh(),
        )

    def _on_add_dept_clicked(self) -> None:
        if not self.state.hospital:
            return
        dlg = AddDepartmentDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return

        payload = dlg.value()
        hid = self.state.hospital.id

        def _do_create():
            return self.state.client.create_department(hid, **payload)

        self.run_action(
            _do_create,
            success_message=f"Department '{payload['name']}' created successfully.",
            on_success=lambda _: self.refresh(),
        )
