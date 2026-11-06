"""Reusable widgets: loading/empty/error states, tables, cards, error presentation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from careflow_desktop.api.errors import ApiError, NetworkError, SessionExpired
from careflow_desktop.ui.models import RecordTableModel, SortRole, StatusBadgeDelegate


class StateStack(QStackedWidget):
    """Shows content, or a loading / empty / error message in its place."""

    def __init__(self, content: QWidget, parent=None):
        super().__init__(parent)
        self._content = content
        self._loading = self._message_page("Loading…")
        self._empty = self._message_page("")
        self._error = QWidget()
        layout = QVBoxLayout(self._error)
        layout.addStretch()
        self._error_label = QLabel(alignment=Qt.AlignCenter, wordWrap=True, objectName="errorText")
        self._retry = QPushButton("Try again")
        self._retry.setFixedWidth(140)
        layout.addWidget(self._error_label)
        layout.addWidget(self._retry, alignment=Qt.AlignCenter)
        layout.addStretch()
        for page in (content, self._loading, self._empty, self._error):
            self.addWidget(page)
        self._retry_cb: Callable[[], None] | None = None
        self._retry.clicked.connect(lambda: self._retry_cb and self._retry_cb())
        self._has_loaded = False

    @staticmethod
    def _message_page(text: str) -> QLabel:
        label = QLabel(text, alignment=Qt.AlignCenter, wordWrap=True, objectName="stateText")
        return label

    def show_loading(self) -> None:
        if not self._has_loaded:  # later refreshes keep the current data visible
            self.setCurrentWidget(self._loading)

    def show_content(self) -> None:
        self._has_loaded = True
        self.setCurrentWidget(self._content)

    def show_empty(self, message: str) -> None:
        self._has_loaded = True
        self._empty.setText(message)
        self.setCurrentWidget(self._empty)

    def show_error(self, message: str, retry: Callable[[], None]) -> None:
        self._error_label.setText(message)
        self._retry_cb = retry
        self.setCurrentWidget(self._error)


class RecordTable(QTableView):
    """Readable, keyboard-friendly table over a RecordTableModel."""

    def __init__(self, model: RecordTableModel, parent=None, sortable: bool = True):
        super().__init__(parent)
        self.record_model = model
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(model)
        self.proxy.setSortRole(SortRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.setModel(self.proxy)
        self.setItemDelegate(StatusBadgeDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(sortable)
        if sortable:
            # Keep the server's order until the user clicks a column header.
            self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)

    def selected_record(self) -> dict | None:
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        return self.record_model.record(self.proxy.mapToSource(rows[0]).row())

    def select_key(self, key_value) -> bool:
        row = self.record_model.row_of(key_value)
        if row < 0:
            return False
        index = self.proxy.mapFromSource(self.record_model.index(row, 0))
        self.selectRow(index.row())
        return True

    def set_rows_keep_selection(self, rows: list[dict], key: str = "id") -> None:
        current = self.selected_record()
        self.record_model.set_rows(rows)
        self.resizeColumnsToContents()
        if current is not None:
            self.select_key(current.get(key))


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent, objectName="statCard")
        layout = QVBoxLayout(self)
        self.value = QLabel("–", objectName="statValue")
        self.title = QLabel(title, objectName="statTitle", wordWrap=True)
        layout.addWidget(self.value)
        layout.addWidget(self.title)

    def set_value(self, value) -> None:
        self.value.setText(str(value))
        self.setAccessibleName(f"{self.title.text()}: {value}")


def error_text(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return exc.user_message
    return "Something went wrong in the desktop application. The problem has been logged."


def show_error(parent: QWidget, exc: Exception, title: str = "Action not completed") -> None:
    """Explain a failed action. Session expiry is handled globally (return to sign-in)."""
    if isinstance(exc, SessionExpired):
        return
    icon = QMessageBox.Warning if isinstance(exc, (ApiError,)) and not isinstance(exc, NetworkError) else \
        QMessageBox.Critical
    box = QMessageBox(icon, title, error_text(exc), QMessageBox.Ok, parent)
    box.exec()


def confirm(parent: QWidget, title: str, text: str, ok_label: str = "Confirm") -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, parent=parent)
    ok = box.addButton(ok_label, QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok


def section_title(text: str) -> QLabel:
    return QLabel(text, objectName="sectionTitle")
