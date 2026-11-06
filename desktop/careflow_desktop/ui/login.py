from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QFrame, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from careflow_desktop.api.errors import ApiError
from careflow_desktop.state import AppState, desk_hospitals
from careflow_desktop.tasks import run_task
from careflow_desktop.ui.widgets import error_text


class LoginView(QWidget):
    logged_in = Signal(dict)  # the /auth/me user payload

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        card = QFrame(objectName="loginCard")
        card.setFixedWidth(400)
        form_box = QVBoxLayout(card)
        title = QLabel("CareFlow", objectName="appTitle", alignment=Qt.AlignCenter)
        subtitle = QLabel("Hospital staff sign-in", alignment=Qt.AlignCenter, objectName="subtle")
        form_box.addWidget(title)
        form_box.addWidget(subtitle)

        form = QFormLayout()
        self.email = QLineEdit(placeholderText="name@hospital.example")
        self.email.setAccessibleName("Email")
        self.password = QLineEdit(echoMode=QLineEdit.Password)
        self.password.setAccessibleName("Password")
        form.addRow("&Email", self.email)
        form.addRow("&Password", self.password)
        form_box.addLayout(form)

        self.message = QLabel(objectName="errorText", wordWrap=True)
        self.message.hide()
        form_box.addWidget(self.message)
        self.submit = QPushButton("Sign in")
        self.submit.setDefault(True)
        form_box.addWidget(self.submit)
        self.server = QLabel(f"Server: {state.client.base_url}", objectName="subtle", alignment=Qt.AlignCenter)
        form_box.addWidget(self.server)

        outer = QVBoxLayout(self)
        outer.addStretch()
        outer.addWidget(card, alignment=Qt.AlignCenter)
        outer.addStretch()

        self.submit.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)
        self.email.returnPressed.connect(self.password.setFocus)

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def reset(self, message: str = "") -> None:
        self.password.clear()
        self._set_busy(False)
        self.show_message(message)
        (self.password if self.email.text() else self.email).setFocus()

    def _set_busy(self, busy: bool) -> None:
        for w in (self.email, self.password, self.submit):
            w.setEnabled(not busy)
        self.submit.setText("Signing in…" if busy else "Sign in")

    def _submit(self) -> None:
        email, password = self.email.text().strip(), self.password.text()
        if not email or not password:
            self.show_message("Enter your email and password.")
            return
        self.show_message("")
        self._set_busy(True)
        client = self.state.client

        def work():
            user = client.login(email, password)
            if not desk_hospitals(user):
                client.logout()
                raise ApiError(403, "not_desk_staff",
                               "This application is for hospital reception and administration staff. "
                               "Doctors and patients should use the CareFlow web app.")
            return user

        def done(user):
            self.password.clear()
            self._set_busy(False)
            self.logged_in.emit(user)

        def failed(exc):
            self._set_busy(False)
            self.password.clear()
            self.password.setFocus()
            self.show_message(error_text(exc))

        run_task(work, done, failed, owner=self)
