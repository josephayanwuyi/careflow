"""Essential desktop workflows, driven through the real widgets with a fake API client."""

from PySide6.QtCore import Qt

from careflow_desktop.api.errors import ApiError, NetworkError
from careflow_desktop.ui import appointments as appointments_module
from careflow_desktop.ui import queue as queue_module
from careflow_desktop.ui.appointments import AppointmentsView
from careflow_desktop.ui.dashboard import DashboardView
from careflow_desktop.ui.dialogs import DuplicateDialog
from careflow_desktop.ui.main_window import MainWindow
from careflow_desktop.ui.patients import RegistrationForm
from careflow_desktop.ui.queue import QueueView
from tests.conftest import DOCTORS, RECEPTIONIST, appointment, duplicate_error


def _window(qtbot, state, config):
    win = MainWindow(state, config)
    qtbot.addWidget(win)
    win.show()
    return win


def _sign_in(qtbot, win):
    win.login.email.setText("desk@example.test")
    win.login.password.setText("secret-password")
    qtbot.mouseClick(win.login.submit, Qt.LeftButton)


# --- Login and session ----------------------------------------------------------------------


def test_login_opens_workspace(qtbot, state, config):
    state.hospital = None
    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    qtbot.waitUntil(lambda: win.pages.currentWidget() is win.workspace, timeout=3000)
    qtbot.waitUntil(lambda: state.hospital is not None and state.hospital.timezone == "Africa/Lagos", timeout=3000)
    assert win.user_label.text() == "Desk Person"
    assert not win.hospital.isVisible()  # single hospital: no selector
    assert win.login.password.text() == ""  # password is not kept in the form
    win.close()


def test_login_error_is_shown(qtbot, state, config, fake):
    fake.login_error = ApiError(401, "invalid_credentials", "The email or password is incorrect.")
    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    qtbot.waitUntil(lambda: win.login.message.isVisible(), timeout=3000)
    assert "incorrect" in win.login.message.text()
    assert win.login.submit.isEnabled()
    assert win.pages.currentWidget() is win.login


def test_login_network_error_is_explained(qtbot, state, config, fake):
    fake.login_error = NetworkError("Cannot reach the CareFlow server. Check the network connection.")
    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    qtbot.waitUntil(lambda: "Cannot reach" in win.login.message.text(), timeout=3000)


def test_doctor_only_account_is_refused(qtbot, state, config, fake):
    fake.user = {**RECEPTIONIST, "memberships": [{**RECEPTIONIST["memberships"][0], "roles": ["doctor"]}]}
    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    qtbot.waitUntil(lambda: "reception and administration staff" in win.login.message.text(), timeout=3000)
    assert ("logout",) in fake.calls


def test_session_expiry_returns_to_login(qtbot, state, config):
    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    qtbot.waitUntil(lambda: win.pages.currentWidget() is win.workspace, timeout=3000)
    state.session_expired.emit()
    qtbot.waitUntil(lambda: win.pages.currentWidget() is win.login, timeout=3000)
    assert "session has ended" in win.login.message.text()


def test_live_update_offline_state_is_visible(qtbot, state, config):
    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    # The fake client has no event stream, so the app must say it is offline and fall back to polling.
    qtbot.waitUntil(lambda: "Offline" in win.connection.text(), timeout=5000)
    assert win._poll.isActive() and win._poll.interval() == 15_000
    win.close()


# --- Dashboard ------------------------------------------------------------------------------


def test_dashboard_shows_counts(qtbot, state):
    view = DashboardView(state)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitUntil(lambda: view.cards["waiting"].value.text() == "3", timeout=3000)
    assert view.cards["with_doctor"].value.text() == "2"
    assert view.cards["pending"].value.text() == "2"
    assert view.alerts.record_model.rowCount() == 1


def test_dashboard_error_then_retry(qtbot, state, fake):
    fake.dashboard_error = NetworkError("Cannot reach the CareFlow server.")
    view = DashboardView(state)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.stack._error, timeout=3000)
    assert "Cannot reach" in view.stack._error_label.text()
    fake.dashboard_error = None
    qtbot.mouseClick(view.stack._retry, Qt.LeftButton)
    qtbot.waitUntil(lambda: view.cards["waiting"].value.text() == "3", timeout=3000)
    assert view.stack.currentWidget() is not view.stack._error


# --- Appointments ----------------------------------------------------------------------------


def _appointments_view(qtbot, state):
    view = AppointmentsView(state)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitUntil(lambda: view.table.record_model.rowCount() == 1, timeout=3000)
    view.table.selectRow(0)
    return view


def test_appointment_details_and_buttons_follow_server_actions(qtbot, state):
    view = _appointments_view(qtbot, state)
    assert "Synthetic Patient" in view.d["patient"].text()
    assert view.btn_approve.isEnabled() and view.btn_reject.isEnabled()
    assert not view.btn_checkin.isEnabled() and not view.btn_noshow.isEnabled()
    qtbot.waitUntil(lambda: view.history.count() == 1, timeout=3000)


def test_approve_sends_expected_version(qtbot, state, fake, monkeypatch):
    monkeypatch.setattr(appointments_module, "confirm", lambda *a, **k: True)
    view = _appointments_view(qtbot, state)
    messages = []
    state.status_message.connect(messages.append)
    qtbot.mouseClick(view.btn_approve, Qt.LeftButton)
    qtbot.waitUntil(lambda: messages == ["Appointment approved."], timeout=3000)
    assert ("appointment_action", "a-1", "approve", {"expected_version": 1}) in fake.calls


def test_approve_cancelled_by_user_sends_nothing(qtbot, state, fake, monkeypatch):
    monkeypatch.setattr(appointments_module, "confirm", lambda *a, **k: False)
    view = _appointments_view(qtbot, state)
    qtbot.mouseClick(view.btn_approve, Qt.LeftButton)
    assert not any(c[0] == "appointment_action" for c in fake.calls)


def test_rejected_action_shows_server_message_and_reloads(qtbot, state, fake, monkeypatch):
    monkeypatch.setattr(appointments_module, "confirm", lambda *a, **k: True)
    shown = []
    monkeypatch.setattr("careflow_desktop.ui.base.show_error", lambda parent, exc, *a: shown.append(exc))
    fake.action_error = ApiError(409, "stale_version", "This appointment was changed by someone else.")
    view = _appointments_view(qtbot, state)
    loads_before = sum(1 for c in fake.calls if c[0] == "list_appointments")
    qtbot.mouseClick(view.btn_approve, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)
    assert shown[0].code == "stale_version"
    qtbot.waitUntil(lambda: sum(1 for c in fake.calls if c[0] == "list_appointments") > loads_before, timeout=3000)


def test_status_filter_is_sent_to_server(qtbot, state, fake):
    view = _appointments_view(qtbot, state)
    view.status.setCurrentIndex(view.status.findText("Approved"))
    qtbot.waitUntil(lambda: fake.calls[-1][0] == "list_appointments" and fake.calls[-1][1]["status"] == ["approved"],
                    timeout=3000)


def test_empty_state_message(qtbot, state, fake):
    fake.appointment_rows = []
    view = AppointmentsView(state)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.stack._empty, timeout=3000)
    assert "No appointments match" in view.stack._empty.text()


def test_live_event_triggers_reload(qtbot, state, fake):
    view = _appointments_view(qtbot, state)
    fake.appointment_rows = [appointment(), appointment(id="a-2")]
    state.server_event.emit({"type": "appointment", "action": "requested"})
    qtbot.waitUntil(lambda: view.table.record_model.rowCount() == 2, timeout=3000)


# --- Live queue --------------------------------------------------------------------------------


def _queue_view(qtbot, state):
    view = QueueView(state)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitUntil(lambda: view.table.record_model.rowCount() == 1, timeout=3000)
    return view


def test_queue_shows_entries_and_estimates(qtbot, state):
    view = _queue_view(qtbot, state)
    model = view.table.record_model
    assert model.data(model.index(0, 2)) == "Synthetic Patient"
    assert model.data(model.index(0, 9)) == "10–20 min (provisional)"
    assert "1 waiting" in view.doctor.itemText(0)
    assert view.btn_call.isEnabled()


def test_call_next(qtbot, state, fake):
    view = _queue_view(qtbot, state)
    qtbot.mouseClick(view.btn_call, Qt.LeftButton)
    qtbot.waitUntil(lambda: ("call_next", "q-1") in fake.calls, timeout=3000)


def test_entry_buttons_follow_allowed_actions_and_permissions(qtbot, state):
    view = _queue_view(qtbot, state)
    view.table.selectRow(0)
    assert view.btn_confirm.isEnabled() and view.btn_transfer.isEnabled() and view.btn_remove.isEnabled()
    assert not view.btn_return.isEnabled()
    # The receptionist lacks queue.priority.set, so the button stays disabled even though the action exists.
    assert not view.btn_priority.isEnabled()


def test_remove_requires_reason_dialog(qtbot, state, fake, monkeypatch):
    class Dialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return True

        def value(self):
            return "Patient left"

    monkeypatch.setattr(queue_module, "ReasonDialog", Dialog)
    monkeypatch.setattr(queue_module, "confirm", lambda *a, **k: True)
    view = _queue_view(qtbot, state)
    view.table.selectRow(0)
    qtbot.mouseClick(view.btn_remove, Qt.LeftButton)
    qtbot.waitUntil(lambda: any(c[0] == "entry_action" for c in fake.calls), timeout=3000)
    call = next(c for c in fake.calls if c[0] == "entry_action")
    assert call[1:] == ("e-1", "cancel", {"expected_version": 1, "reason": "Patient left"})


def test_queue_empty_for_doctor_without_queue(qtbot, state):
    view = _queue_view(qtbot, state)
    view.doctor.setCurrentIndex(1)  # Dr. Bayo has no queue today
    qtbot.waitUntil(lambda: view.stack.currentWidget() is view.stack._empty, timeout=3000)
    assert "Dr. Bayo" in view.stack._empty.text()
    assert not view.btn_call.isEnabled()


# --- Patient registration ------------------------------------------------------------------------


def test_registration_success(qtbot, state, fake):
    fake.register_responses = [{"id": "p-5", "full_name": "New Person", "hospital_patient_number": "TEST-000005"}]
    registered = []
    form = RegistrationForm(state, registered.append)
    qtbot.addWidget(form)
    form.name.setText("  New   Person ")
    form.phone.setText("0803 000 0005")
    qtbot.mouseClick(form.submit, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(registered), timeout=3000)
    assert fake.calls[-1] == ("register_patient", {"full_name": "New Person", "phone": "0803 000 0005",
                                                   "date_of_birth": None, "sex": None})
    assert form.name.text() == ""


def test_registration_validates_name_locally(qtbot, state, fake):
    form = RegistrationForm(state, lambda p: None)
    qtbot.addWidget(form)
    form.show()
    qtbot.mouseClick(form.submit, Qt.LeftButton)
    assert form.error.isVisible() and not fake.calls


def test_duplicate_can_be_registered_anyway_with_reason(qtbot, state, fake, monkeypatch):
    fake.register_responses = [duplicate_error(),
                               {"id": "p-6", "full_name": "Same Name", "hospital_patient_number": "TEST-000006"}]

    def fake_exec(dialog):
        dialog.reason.setText("Different person, shared phone")
        return DuplicateDialog.REGISTER_ANYWAY

    monkeypatch.setattr(DuplicateDialog, "exec", fake_exec)
    registered = []
    form = RegistrationForm(state, registered.append)
    qtbot.addWidget(form)
    form.name.setText("Same Name")
    qtbot.mouseClick(form.submit, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(registered), timeout=3000)
    second = fake.calls[-1][1]
    assert second["confirm_not_duplicate"] is True
    assert second["duplicate_override_reason"] == "Different person, shared phone"


def test_duplicate_can_use_existing_patient(qtbot, state, fake, monkeypatch):
    fake.register_responses = [duplicate_error()]
    monkeypatch.setattr(DuplicateDialog, "exec", lambda dialog: DuplicateDialog.USE_EXISTING)
    chosen = []
    form = RegistrationForm(state, chosen.append)
    qtbot.addWidget(form)
    form.name.setText("Same Name")
    qtbot.mouseClick(form.submit, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(chosen), timeout=3000)
    assert chosen[0]["id"] == "p-9" and len(fake.calls) == 1


def test_doctor_directory_fixture_sanity():
    assert {d["id"] for d in DOCTORS} == {"d-1", "d-2"}


def test_admin_staff_management_and_notification_reports(qtbot, state, config, fake, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from careflow_desktop.ui.admin import AdminView, AddStaffDialog, AddDepartmentDialog

    win = _window(qtbot, state, config)
    _sign_in(qtbot, win)
    qtbot.waitUntil(lambda: win.pages.currentWidget() is win.workspace, timeout=3000)

    # Switch to Admin & Staff tab (item index 6)
    win.nav.setCurrentRow(6)
    admin_view = win.views.currentWidget()
    assert isinstance(admin_view, AdminView)

    # Verify initial data loaded: staff table, department table, notification cards
    qtbot.waitUntil(lambda: admin_view.staff_table.rowCount() == 1, timeout=3000)
    assert admin_view.dept_table.rowCount() == 1
    assert admin_view.card_sms.value.text() == "42"
    assert admin_view.card_email.value.text() == "88"
    assert admin_view.card_push.value.text() == "110"
    assert admin_view.notif_table.rowCount() == 1

    # Simulate opening AddStaffDialog to add a receptionist
    def _mock_staff_dialog(dialog_self):
        dialog_self.full_name.setText("Jessica Alba")
        dialog_self.email.setText("jessica@test.org")
        dialog_self.password.setText("SecretPass123")
        return QDialog.Accepted

    monkeypatch.setattr(AddStaffDialog, "exec", _mock_staff_dialog)
    admin_view._on_add_staff_clicked()

    qtbot.waitUntil(lambda: any(c[0] == "create_staff" for c in fake.calls), timeout=3000)
    call = [c for c in fake.calls if c[0] == "create_staff"][0]
    assert call[2]["full_name"] == "Jessica Alba"
    assert call[2]["email"] == "jessica@test.org"
    assert call[2]["roles"] == ["receptionist"]
    qtbot.waitUntil(lambda: admin_view.staff_table.rowCount() == 2, timeout=3000)

    # Simulate opening AddDepartmentDialog
    def _mock_dept_dialog(dialog_self):
        dialog_self.name_edit.setText("Cardiology")
        dialog_self.desc_edit.setText("Heart care")
        return QDialog.Accepted

    monkeypatch.setattr(AddDepartmentDialog, "exec", _mock_dept_dialog)
    admin_view._on_add_dept_clicked()

    qtbot.waitUntil(lambda: any(c[0] == "create_department" for c in fake.calls), timeout=3000)
    qtbot.waitUntil(lambda: admin_view.dept_table.rowCount() == 2, timeout=3000)

    win.close()

