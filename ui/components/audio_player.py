"""Reusable audio preview widget backed by QtMultimedia.

QtMultimedia is imported lazily-ish (module level with a guard) so the window
still opens on systems where the multimedia plugin is missing; playback then
reports a friendly error instead of crashing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ui import theme
from ui.components.icons import set_icon

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    _HAS_MULTIMEDIA = True
except Exception:  # pragma: no cover - depends on the installed PySide6 build
    QAudioOutput = QMediaPlayer = None  # type: ignore[assignment]
    _HAS_MULTIMEDIA = False


class AudioPlayer(QObject):
    """Thin wrapper that keeps the QAudioOutput alive."""

    state_changed = Signal(bool)   # True while playing
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._player = None
        self._output = None
        if not _HAS_MULTIMEDIA:
            return
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._output.setVolume(0.9)
        self._player.setAudioOutput(self._output)
        self._player.playbackStateChanged.connect(
            lambda state: self.state_changed.emit(
                state == QMediaPlayer.PlaybackState.PlayingState
            )
        )
        self._player.errorOccurred.connect(self._on_error)

    def play(self, path: str | Path) -> None:
        if self._player is None:
            self.error.emit("Thiếu QtMultimedia — không phát được audio trong app.")
            return
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def is_playing(self) -> bool:
        return bool(self._player is not None and self._player.isPlaying())

    def _on_error(self, _error, message: str) -> None:
        if message:
            self.error.emit(f"Không phát được audio: {message}")


class AudioPreview(QWidget):
    """Path label plus play/stop buttons, reused in Project and TTS tabs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AudioPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._path: Optional[Path] = None
        self._level = ""
        self.player = AudioPlayer(self)
        self.player.state_changed.connect(self._on_state)
        self.player.error.connect(self._on_error)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(8)
        self.label = QLabel("Chưa có audio để nghe")
        self.label.setObjectName("MutedLabel")
        self.label.setMinimumWidth(180)
        self.play_button = QPushButton("Nghe")
        self.play_button.setObjectName("smallButton")
        set_icon(self.play_button, "play", "text", 13)
        self.play_button.setEnabled(False)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setObjectName("smallButton")
        set_icon(self.stop_button, "stop", "muted", 12)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.play_button)
        layout.addWidget(self.stop_button)

        self.play_button.clicked.connect(self.toggle)
        self.stop_button.clicked.connect(self.stop)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def set_file(self, path: str | Path | None) -> None:
        candidate = Path(path) if path else None
        if candidate is not None and not candidate.is_file():
            candidate = None
        self._path = candidate
        self.play_button.setEnabled(candidate is not None)
        if candidate is None:
            self.label.setText("Chưa có audio để nghe")
            self.label.setToolTip("")
        else:
            self.label.setText(candidate.name)
            self.label.setToolTip(str(candidate))
        self.stop()

    def play(self) -> None:
        if self._path is not None:
            self.player.play(self._path)

    def toggle(self) -> None:
        if self._path is None:
            return
        if self.player.is_playing():
            self.stop()
        else:
            self.play()

    def stop(self) -> None:
        self.player.stop()
        self._set_level("")
        self.stop_button.setEnabled(False)

    def _set_level(self, level: str) -> None:
        self._level = level
        if level == "success":
            self.label.setStyleSheet(f"color:{theme.SUCCESS};")
        elif level == "error":
            self.label.setStyleSheet(f"color:{theme.DANGER};")
        else:
            self.label.setStyleSheet("")

    def apply_theme(self) -> None:
        self._set_level(self._level)

    def _on_state(self, playing: bool) -> None:
        self._set_level("success" if playing else "")
        self.stop_button.setEnabled(playing)

    def _on_error(self, message: str) -> None:
        self._set_level("error")
        self.label.setText(message)
        self.label.setToolTip(message)
