"""Application stylesheet: a calm, high-contrast look with clear focus indicators."""

STYLESHEET = """
QWidget { font-size: 10pt; color: #1F1F1F; }
QMainWindow, QStackedWidget { background: #F5F7FA; }
QLabel#brand { font-size: 14pt; font-weight: 700; color: #0B4F8A; padding-right: 12px; }
QLabel#appTitle { font-size: 20pt; font-weight: 700; color: #0B4F8A; }
QLabel#subtle, QLabel#stateText { color: #555555; }
QLabel#stateText { font-size: 11pt; padding: 24px; }
QLabel#errorText { color: #A4262C; font-weight: 600; }
QLabel#warningText { color: #8A4B00; font-weight: 600; }
QLabel#sectionTitle { font-size: 11pt; font-weight: 700; margin-top: 8px; }
QLabel#badgeLabel { font-weight: 600; padding: 2px 8px; }
QFrame#loginCard, QFrame#detailsPanel, QFrame#statCard {
    background: white; border: 1px solid #D6DCE4; border-radius: 8px; padding: 12px;
}
QLabel#statValue { font-size: 24pt; font-weight: 700; color: #0B4F8A; }
QLabel#statTitle { color: #444444; }
QListWidget#nav { background: white; border: 1px solid #D6DCE4; border-radius: 6px; font-size: 11pt; }
QListWidget#nav::item { padding: 10px 8px; }
QListWidget#nav::item:selected { background: #0B4F8A; color: white; }
QTableView { background: white; alternate-background-color: #F2F5F9; gridline-color: #E3E8EF;
             selection-background-color: #CFE2F7; selection-color: #1F1F1F; }
QHeaderView::section { background: #E9EEF4; padding: 6px; border: none; border-right: 1px solid #D6DCE4;
                       font-weight: 600; }
QPushButton, QToolButton { padding: 6px 12px; border: 1px solid #9AA8B8; border-radius: 4px; background: white; }
QPushButton:hover, QToolButton:hover { background: #EEF4FB; }
QPushButton:default { background: #0B4F8A; color: white; border-color: #0B4F8A; }
QPushButton:disabled, QToolButton:disabled { color: #8A8A8A; border-color: #CCCCCC; background: #F3F3F3; }
*:focus { outline: none; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus,
QPlainTextEdit:focus, QTableView:focus, QListWidget:focus { border: 2px solid #0B6BCB; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit { padding: 4px; border: 1px solid #9AA8B8;
                                                             border-radius: 4px; background: white; }
QLabel#connection[state="live"] { color: #1E5E1E; }
QLabel#connection[state="connecting"] { color: #8A4B00; }
QLabel#connection[state="offline"] { color: #A4262C; }
"""
