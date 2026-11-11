"""AutoYout entry point: Text → TTS → Video → YouTube."""
from __future__ import annotations

import ctypes
import io
import logging
import os
import sys
import traceback
import warnings
from pathlib import Path

# Suppress benign future deprecation warnings (e.g. google.api_core python version notices)
warnings.filterwarnings("ignore", category=FutureWarning)

# Provide safe dummy stream when stdout/stderr are None (e.g. in PyInstaller windowed apps)
class _SafeStream(io.StringIO):
    def write(self, s: str) -> int:
        return len(s) if s else 0
    def flush(self) -> None:
        pass

if sys.stdout is None:
    sys.stdout = _SafeStream()
if sys.stderr is None:
    sys.stderr = _SafeStream()

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.settings import load_settings  # noqa: E402
from ui.components.icons import brand_icon  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402


def _resolve_log_dir(settings: dict) -> Path:
    raw = str((settings.get("app") or {}).get("log_dir") or "logs").strip() or "logs"
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _set_app_id() -> None:
    """Give Windows a stable taskbar identity so the icon groups correctly."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoYout.Pipeline.1")
    except Exception:
        pass


def _install_excepthook(app: QApplication, logger: logging.Logger) -> None:
    def handle(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error("Lỗi không mong đợi:\n%s", details)
        try:
            QMessageBox.critical(
                None,
                "AutoYout gặp lỗi",
                f"{exc_type.__name__}: {exc_value}\n\nChi tiết đã được ghi vào log.",
            )
        except Exception:
            pass

    sys.excepthook = handle


def main() -> int:
    settings = load_settings()
    logger = setup_logging(_resolve_log_dir(settings), logging.INFO)
    logger.info("Khởi động AutoYout")

    _set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("AutoYout")
    app.setApplicationDisplayName("AutoYout")
    app.setOrganizationName("AutoYout")
    app.setWindowIcon(brand_icon())
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    _install_excepthook(app, logger)

    window = MainWindow(settings)
    window.setWindowIcon(brand_icon())
    window.show()
    code = app.exec()
    logger.info("Thoát AutoYout (exit=%s)", code)
    logging.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
