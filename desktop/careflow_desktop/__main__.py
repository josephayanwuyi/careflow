"""Start the CareFlow desktop app:  python -m careflow_desktop

Environment:
  CAREFLOW_API_URL          API server (default http://127.0.0.1:8000)
  CAREFLOW_TIMEOUT_SECONDS  request timeout (default 10)
  CAREFLOW_REFRESH_SECONDS  fallback refresh interval (default 60)
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication

from careflow_desktop import __version__
from careflow_desktop.api.client import ApiClient
from careflow_desktop.config import Config
from careflow_desktop.state import AppState
from careflow_desktop.ui.main_window import MainWindow
from careflow_desktop.ui.style import STYLESHEET


def _configure_logging() -> Path:
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CareFlow" / "desktop-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "careflow-desktop.log", maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
    logging.getLogger("httpx").setLevel(logging.WARNING)  # never log request URLs/headers at INFO
    return log_dir


def main() -> int:
    _configure_logging()
    config = Config.from_env()
    app = QApplication(sys.argv)
    app.setApplicationName("CareFlow Desk")
    app.setApplicationVersion(__version__)
    app.setStyleSheet(STYLESHEET)
    client = ApiClient(config.api_url, timeout=config.request_timeout)
    state = AppState(client)
    window = MainWindow(state, config)
    window.show()
    code = app.exec()
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
