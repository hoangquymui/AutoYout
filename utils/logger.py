"""Logging for both the log file and the Qt console.

A single ``logging`` tree feeds a rotating file handler and (when Qt is
available) a Qt handler that re-emits every record as a signal, so worker
threads never touch widgets directly.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

try:  # Qt is optional for headless tests/tools
    from PySide6.QtCore import QObject, Signal

    _HAS_QT = True
except Exception:  # pragma: no cover - exercised only without PySide6
    _HAS_QT = False
    QObject = object  # type: ignore[assignment,misc]
    Signal = None    # type: ignore[assignment]

APP_LOGGER_NAME = "AutoYout"
_FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_CONSOLE_FORMAT = "%(message)s"


if _HAS_QT:

    class LogEmitter(QObject):
        """Carries ``(level, message)`` from any thread to the GUI thread."""

        message = Signal(str, str)


    class QtLogHandler(logging.Handler):
        """Logging handler that forwards records through a Qt signal."""

        def __init__(self, emitter: "LogEmitter", level: int = logging.INFO) -> None:
            super().__init__(level)
            self.emitter = emitter

        def emit(self, record: logging.LogRecord) -> None:
            try:
                self.emitter.message.emit(record.levelname, record.getMessage())
            except RuntimeError:
                # Emitter destroyed (window closed) while a worker still logs.
                pass


    class QtStreamWriter:
        """Redirects stdout / stderr writes to a Qt LogEmitter signal."""

        def __init__(self, emitter: "LogEmitter", level: str = "INFO", fallback=None) -> None:
            self.emitter = emitter
            self.level = level
            self.fallback = fallback
            self._buffer = ""

        def write(self, text: str) -> int:
            if not text:
                return 0
            if self.fallback and hasattr(self.fallback, "write"):
                try:
                    self.fallback.write(text)
                except Exception:
                    pass
            self._buffer += str(text)
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.strip("\r")
                if line.strip():
                    try:
                        self.emitter.message.emit(self.level, line)
                    except Exception:
                        pass
            return len(text)

        def flush(self) -> None:
            if self._buffer.strip():
                try:
                    self.emitter.message.emit(self.level, self._buffer.strip("\r"))
                except Exception:
                    pass
                self._buffer = ""
            if self.fallback and hasattr(self.fallback, "flush"):
                try:
                    self.fallback.flush()
                except Exception:
                    pass

else:  # pragma: no cover
    LogEmitter = None  # type: ignore[assignment]
    QtLogHandler = None  # type: ignore[assignment]
    QtStreamWriter = None  # type: ignore[assignment]


def setup_logging(log_dir: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    """Configure the app logger once; safe to call repeatedly."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if getattr(logger, "_autoyout_ready", False):
        return logger

    if log_dir:
        try:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "autoyout.log",
                maxBytes=4 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
            logger.addHandler(file_handler)
        except OSError:
            pass

    if sys.stderr is not None:
        try:
            stream = logging.StreamHandler(sys.stderr)
            stream.setLevel(level)
            stream.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
            logger.addHandler(stream)
        except Exception:
            pass
    logger._autoyout_ready = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: str) -> logging.Logger:
    """Child logger under the app tree (``AutoYout.core.tts``)."""
    return logging.getLogger(f"{APP_LOGGER_NAME}.{name}")


def attach_qt_handler(logger: logging.Logger, emitter: "LogEmitter", level: int = logging.INFO) -> Optional["QtLogHandler"]:
    if not _HAS_QT:  # pragma: no cover
        return None
    handler = QtLogHandler(emitter, level)
    logger.addHandler(handler)
    return handler


def detach_handler(logger: logging.Logger, handler: Optional[logging.Handler]) -> None:
    if handler is None:
        return
    try:
        logger.removeHandler(handler)
        handler.close()
    except Exception:
        pass
