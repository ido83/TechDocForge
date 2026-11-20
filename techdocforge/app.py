"""Application entry point."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_NAME, __version__
from .config import log_dir


def _setup_logging() -> None:
    # Document contents, prompts and API keys are never logged.
    handler = RotatingFileHandler(log_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def hook(exc_type, exc, tb):
        logging.getLogger(APP_NAME).critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def main() -> int:
    _setup_logging()
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .gui import MainWindow

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TechDocForge.Desktop")
        except (AttributeError, OSError):
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)

    project_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    icon_path = project_root / "assets" / "TechDocForge.png"
    app_icon = QIcon(str(icon_path))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
