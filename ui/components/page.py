"""Small layout helpers shared by the tab pages."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.components.icons import set_icon


class PageHeader(QWidget):
    """Page title with a one-line description and optional trailing actions."""

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        text_col.addWidget(self.title_label)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("PageSubtitle")
            self.subtitle_label.setWordWrap(True)
            text_col.addWidget(self.subtitle_label)
        layout.addLayout(text_col, 1)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        layout.addLayout(self._actions, 0)

    def add_action(self, widget: QWidget) -> None:
        self._actions.addWidget(widget)


def wrap_scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName("PageScroll")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setWidget(content)
    return area


def stack_columns(owner: QWidget, layout: QBoxLayout, breakpoint: int) -> None:
    """Stack a two-column layout vertically once the page is narrower than ``breakpoint``."""
    direction = (QBoxLayout.Direction.TopToBottom
                 if owner.width() < breakpoint
                 else QBoxLayout.Direction.LeftToRight)
    if layout.direction() != direction:
        layout.setDirection(direction)


def scroll_page(inner: QWidget, margins: int = 14, spacing: int = 12) -> QWidget:
    """Wrap ``inner`` in a transparent vertically scrolling page."""
    content = QWidget()
    content.setObjectName("PageContent")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    layout.addWidget(inner)
    layout.addStretch(1)
    return wrap_scroll(content)


def icon_button(
    name: str,
    tooltip: str,
    *,
    role: str = "muted",
    size: int = 17,
    object_name: str = "iconButton",
    side: int = 32,
) -> QPushButton:
    button = QPushButton()
    button.setObjectName(object_name)
    set_icon(button, name, role, size)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setFixedSize(side, side)
    return button


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PanelTitle")
    return label


def paint_status(label: QLabel, level: str) -> None:
    label.setProperty("ayStatusLevel", level)
    label.setStyleSheet(f"color:{_level_color(level)};")


def refresh_statuses(root: QWidget) -> None:
    """Repaint inline status colours after a theme switch."""
    for label in root.findChildren(QLabel):
        level = label.property("ayStatusLevel")
        if level:
            label.setStyleSheet(f"color:{_level_color(str(level))};")


def _level_color(level: str) -> str:
    return {
        "info": theme.TEXT_MUTED,
        "running": theme.BLUE,
        "success": theme.SUCCESS,
        "warning": theme.WARNING,
        "error": theme.DANGER,
        "muted": theme.TEXT_DIM,
    }.get(level, theme.TEXT_MUTED)
