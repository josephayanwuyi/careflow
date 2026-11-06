"""Qt model/view support: a table model over API records and a status-badge delegate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from careflow_desktop.formatting import STATUS_COLORS, status_label

RecordRole = Qt.UserRole + 1
StatusRole = Qt.UserRole + 2
SortRole = Qt.UserRole + 3


@dataclass
class Column:
    header: str
    value: Callable[[dict], Any]
    status: Callable[[dict], str | None] | None = None   # render as a coloured badge
    align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter
    sort: Callable[[dict], Any] | None = None
    tooltip: Callable[[dict], str | None] | None = None


class RecordTableModel(QAbstractTableModel):
    """Read-only table over a list of dicts; records are replaced wholesale on refresh."""

    def __init__(self, columns: list[Column], key: str = "id", parent=None):
        super().__init__(parent)
        self._columns = columns
        self._rows: list[dict] = []
        self._key = key

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def append_rows(self, rows: list[dict]) -> None:
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows) + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def rows(self) -> list[dict]:
        return list(self._rows)

    def record(self, row: int) -> dict | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, key_value) -> int:
        return next((i for i, r in enumerate(self._rows) if r.get(self._key) == key_value), -1)

    # Qt API
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._columns[section].header
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        rec, col = self._rows[index.row()], self._columns[index.column()]
        if role == Qt.DisplayRole:
            if col.status:
                return status_label(col.status(rec))
            value = col.value(rec)
            return "" if value is None else str(value)
        if role == StatusRole and col.status:
            return col.status(rec)
        if role == RecordRole:
            return rec
        if role == SortRole:
            return (col.sort or col.value)(rec)
        if role == Qt.TextAlignmentRole:
            return int(col.align)
        if role == Qt.ToolTipRole and col.tooltip:
            return col.tooltip(rec)
        return None


class StatusBadgeDelegate(QStyledItemDelegate):
    """Paints statuses as rounded, coloured badges. The text is always shown too (not colour alone)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        status = index.data(StatusRole)
        if not status:
            super().paint(painter, option, index)
            return
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        bg, fg = STATUS_COLORS.get(status, ("#E8E8E8", "#333333"))
        text = index.data(Qt.DisplayRole)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = min(metrics.horizontalAdvance(text) + 16, option.rect.width() - 8)
        rect = QRectF(option.rect.x() + 4, option.rect.center().y() - 10, width, 20)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor(bg))
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignCenter, metrics.elidedText(text, Qt.ElideRight, int(width) - 8))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width() + 20, max(size.height(), 26))
