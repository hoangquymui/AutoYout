"""Global stylesheet for AutoYout, generated from the active theme palette."""
from __future__ import annotations

from pathlib import Path
from string import Template

from ui import theme

ICONS_DIR = Path(__file__).resolve().parent / "assets" / "icons"

_QSS = """
* {
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
}
QMainWindow, QWidget#Root {
    background: $BG;
    color: $TEXT;
}
QWidget#PageContent, QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}
QScrollArea { background: transparent; border: none; }
QFrame#Header {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 $HEADER_FROM, stop:1 $HEADER_TO);
    border: 1px solid $BORDER;
    border-radius: 16px;
}
QFrame#BottomPanel {
    background: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: 16px;
}
QSplitter#MainSplitter { background: transparent; }
QSplitter#MainSplitter::handle { background: transparent; }
QSplitter#MainSplitter::handle:hover { background: $BORDER; border-radius: 3px; }
QFrame#ProjectCard {
    background: $PROJECT_CARD_BG;
    border: 1px solid $BORDER;
    border-radius: 10px;
}
QFrame#Toolbar {
    background: $SURFACE_ALT;
    border: 1px solid $BORDER;
    border-radius: 10px;
}
QFrame#StatusBadge {
    background: $BADGE_BG;
    border: 1px solid $BORDER;
    border-radius: 10px;
}
QFrame#EditorToolbar {
    background: $SURFACE_ALT;
    border: 1px solid $BORDER;
    border-radius: 12px;
}
QFrame#ToolbarSep { background: $BORDER; border: none; }
QFrame#EditorPanel {
    background: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: 14px;
}
QFrame#PlaybackBar {
    background: $SURFACE_ALT;
    border: 1px solid $BORDER;
    border-radius: 12px;
}
QFrame#SummaryBar {
    background: $SURFACE_ALT;
    border: 1px solid $BORDER;
    border-radius: 12px;
}
QFrame#FilesPanel {
    background: $GROUP_BG;
    border: 1px solid $GROUP_BORDER;
    border-radius: 12px;
}
QFrame#PanelSep { background: $BORDER; border: none; }
QLabel#Timecode {
    color: $TEXT_MUTED;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 9pt;
}
QPushButton#presetCard {
    text-align: left;
    padding: 8px 10px;
    font-weight: 600;
    background: $GROUP_BG;
    border: 1px solid $GROUP_BORDER;
    border-radius: 10px;
}
QScrollArea#InspectorScroll,
QScrollArea#InspectorScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}
QPushButton#presetCard:hover {
    background: $BUTTON_HOVER;
    border-color: $ACCENT;
}
QPushButton#presetCard:pressed { background: $BUTTON_PRESSED; }
QListWidget#LayerList {
    background: transparent;
    border: none;
    padding: 0;
}
QListWidget#LayerList::item {
    padding: 7px 8px;
    margin: 1px 0;
    border-radius: 8px;
}
QListWidget#LayerList::item:hover { background: $TABLE_HOVER; }
QListWidget#LayerList::item:selected {
    background: $TABLE_SELECTED;
    color: $TABLE_SELECTED_TEXT;
}
QSlider#TimelineSlider::groove:horizontal { height: 5px; }
QSlider#TimelineSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -6px 0;
}
QWidget#AudioPreview {
    background: $AUDIO_PREVIEW_BG;
    border: 1px solid $BORDER;
    border-radius: 9px;
}
QLabel { color: $TEXT; background: transparent; }
QLabel#BrandTitle { font-size: 18pt; font-weight: 700; color: $TEXT_STRONG; }
QLabel#BrandSubtitle { font-size: 8pt; color: $TEXT_DIM; }
QLabel#ProjectCaption { font-size: 7.5pt; font-weight: 700; color: $TEXT_CAPTION; }
QLabel#FieldLabel { font-size: 8pt; font-weight: 600; color: $TEXT_SUBTLE; }
QLabel#ProjectPath { font-size: 9.5pt; color: $TEXT_BODY; }
QLabel#BadgeTitle { font-size: 7.5pt; font-weight: 700; color: $TEXT_CAPTION; }
QLabel#BadgeValue { font-size: 8.5pt; font-weight: 600; color: $TEXT; }
QLabel#PageTitle { font-size: 14pt; font-weight: 700; color: $TEXT_TITLE; }
QLabel#PageSubtitle { font-size: 9pt; color: $TEXT_SUBTLE; }
QLabel#PanelTitle { font-size: 8pt; font-weight: 700; color: $TEXT_PANEL; }
QLabel#LogCount { font-size: 8pt; color: $TEXT_LOGCOUNT; }
QLabel#MutedLabel { color: $TEXT_SUBTLE; }
QLabel#SummaryLabel { color: $TEXT_MUTED; }
QLabel#StatusText { color: $TEXT_MUTED; }
QLabel#StageChip {
    background: $STAGE_BG;
    border: 1px solid $STAGE_BORDER;
    border-radius: 10px;
    padding: 3px 10px;
    color: $STAGE_TEXT;
    font-weight: 600;
    font-size: 8.5pt;
}
QLabel#PercentLabel { color: $TEXT_PERCENT; font-weight: 600; font-size: 9pt; }
QTabWidget::pane {
    border: 1px solid $BORDER;
    border-radius: 14px;
    background: $SURFACE;
    top: 8px;
}
QTabBar {
    background: transparent;
    qproperty-drawBase: 0;
}
QTabBar::tab {
    background: transparent;
    color: $TAB_TEXT;
    padding: 8px 13px;
    margin-right: 3px;
    border: 1px solid transparent;
    border-radius: 8px;
}
QTabBar::tab:selected {
    background: $TAB_SELECTED_BG;
    border-color: $TAB_SELECTED_BORDER;
    color: $TAB_SELECTED_TEXT;
}
QTabBar::tab:hover { color: $TAB_TEXT_HOVER; background: $TAB_HOVER_BG; }
QTabBar::tab:disabled { color: $TAB_TEXT_DISABLED; }
QGroupBox {
    border: 1px solid $GROUP_BORDER;
    border-radius: 12px;
    margin-top: 0px;
    padding: 30px 12px 9px 12px;
    background: $GROUP_BG;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 8px;
    padding: 0;
    background: transparent;
    color: $GROUP_TITLE_TEXT;
    font-weight: 600;
    font-size: 9.5pt;
}
QGroupBox#headerlessGroup { padding: 9px 12px 9px 12px; }
QLabel#GroupTitle { color: $GROUP_TITLE_TEXT; font-weight: 600; font-size: 9.5pt; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateTimeEdit, QListWidget {
    background: $INPUT;
    border: 1px solid $BORDER_CARD;
    border-radius: 8px;
    padding: 5px 9px;
    color: $TEXT;
    selection-background-color: $TABLE_SELECTED;
    selection-color: $TABLE_SELECTED_TEXT;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateTimeEdit:focus, QListWidget:focus {
    border: 1px solid $ACCENT;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QDateTimeEdit:disabled, QListWidget:disabled {
    color: $BUTTON_DISABLED_TEXT;
    background: $INPUT_DISABLED;
    border-color: $BORDER;
}
QComboBox { padding-right: 28px; }
QComboBox::drop-down {
    border: none;
    width: 26px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}
QComboBox::down-arrow {
    image: url($CHEVRON_DOWN);
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    background: $POPUP_BG;
    border: 1px solid $POPUP_BORDER;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: $POPUP_SELECTED;
    selection-color: $POPUP_SELECTED_TEXT;
    color: $POPUP_TEXT;
    outline: 0;
}
QComboBox QAbstractItemView::item { padding: 6px 8px; border-radius: 6px; }
QSpinBox, QDoubleSpinBox { padding-right: 24px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    background: $SPIN_BG;
    border-left: 1px solid $BORDER;
    border-top-right-radius: 7px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    background: $SPIN_BG;
    border-left: 1px solid $BORDER;
    border-top: 1px solid $BORDER;
    border-bottom-right-radius: 7px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url($CHEVRON_UP);
    width: 10px;
    height: 10px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url($CHEVRON_DOWN);
    width: 10px;
    height: 10px;
}
QDateTimeEdit { padding-right: 26px; }
QDateTimeEdit::drop-down {
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}
QDateTimeEdit::down-arrow {
    image: url($CHEVRON_DOWN);
    width: 12px;
    height: 12px;
}
QListWidget::item { padding: 5px 7px; border-radius: 6px; }
QListWidget::item:selected { background: $POPUP_SELECTED; color: $POPUP_SELECTED_TEXT; }
QPushButton {
    background: $BUTTON_BG;
    color: $BUTTON_TEXT;
    border: 1px solid $BUTTON_BORDER;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 500;
}
QPushButton:hover { background: $BUTTON_HOVER; border-color: $BUTTON_HOVER_BORDER; }
QPushButton:pressed { background: $BUTTON_PRESSED; }
QPushButton:focus { border-color: $ACCENT; }
QPushButton:disabled {
    color: $BUTTON_DISABLED_TEXT;
    background: $BUTTON_DISABLED_BG;
    border-color: $BUTTON_DISABLED_BORDER;
}
QPushButton#primaryButton {
    background: $PRIMARY_BG;
    border-color: $PRIMARY_BORDER;
    color: $PRIMARY_TEXT;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: $PRIMARY_HOVER; }
QPushButton#primaryButton:pressed { background: $PRIMARY_PRESSED; }
QPushButton#primaryButton:disabled {
    background: $PRIMARY_DISABLED_BG;
    border-color: $PRIMARY_DISABLED_BORDER;
    color: $PRIMARY_DISABLED_TEXT;
}
QPushButton#runAllButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 $RUN_FROM, stop:0.55 $RUN_MID, stop:1 $RUN_TO);
    border: 1px solid $RUN_BORDER;
    color: $RUN_TEXT;
    font-weight: 800;
    font-size: 9.5pt;
    padding: 9px 10px;
}
QPushButton#runAllButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 $RUN_HOVER_FROM, stop:0.55 $RUN_HOVER_MID, stop:1 $RUN_HOVER_TO);
    border-color: $RUN_HOVER_BORDER;
}
QPushButton#runAllButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 $RUN_PRESS_FROM, stop:0.55 $RUN_PRESS_MID, stop:1 $RUN_PRESS_TO);
}
QPushButton#runAllButton:disabled {
    background: $RUN_DISABLED_BG;
    border-color: $RUN_DISABLED_BORDER;
    color: $RUN_DISABLED_TEXT;
}
QPushButton#pauseButton {
    background: $PAUSE_BG;
    border-color: $PAUSE_BORDER;
    color: $PAUSE_TEXT;
}
QPushButton#pauseButton:hover { background: $PAUSE_HOVER; border-color: $PAUSE_HOVER_BORDER; }
QPushButton#pauseButton:pressed { background: $PAUSE_PRESSED; }
QPushButton#pauseButton:disabled {
    background: $BUTTON_DISABLED_BG;
    border-color: $BUTTON_DISABLED_BORDER;
    color: $BUTTON_DISABLED_TEXT;
}
QPushButton#stopButton {
    background: $STOP_BG;
    border-color: $STOP_BORDER;
    color: $STOP_TEXT;
}
QPushButton#stopButton:hover { background: $STOP_HOVER; border-color: $STOP_HOVER_BORDER; }
QPushButton#stopButton:pressed { background: $STOP_PRESSED; }
QPushButton#stopButton:disabled {
    background: $BUTTON_DISABLED_BG;
    border-color: $BUTTON_DISABLED_BORDER;
    color: $BUTTON_DISABLED_TEXT;
}
QPushButton#smallButton {
    padding: 4px 10px;
    font-size: 8.5pt;
    background: $SMALL_BG;
    border-color: $SMALL_BORDER;
}
QPushButton#smallButton:hover { background: $BUTTON_HOVER; border-color: $BUTTON_HOVER_BORDER; }
QPushButton#iconButton {
    padding: 0;
    background: $ICONBTN_BG;
    border: 1px solid $ICONBTN_BORDER;
    border-radius: 8px;
}
QPushButton#iconButton:hover { background: $ICONBTN_HOVER; border-color: $BUTTON_HOVER_BORDER; }
QPushButton#iconButton:pressed { background: $ICONBTN_PRESSED; }
QPushButton#iconButton:disabled {
    background: $BUTTON_DISABLED_BG;
    border-color: $BUTTON_DISABLED_BORDER;
}
QPushButton#variableButton {
    padding: 2px 8px;
    font-size: 8pt;
    color: $VARIABLE_TEXT;
    background: $VARIABLE_BG;
    border-color: $VARIABLE_BORDER;
    border-radius: 6px;
}
QPushButton#variableButton:hover { background: $VARIABLE_HOVER; border-color: $VARIABLE_HOVER_BORDER; }
QCheckBox { color: $TEXT_MUTED; spacing: 9px; }
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid $CHECK_BORDER;
    border-radius: 5px;
    background: $CHECK_BG;
}
QCheckBox::indicator:hover { border-color: $ACCENT; }
QCheckBox::indicator:checked {
    background: $ACCENT_STRONG;
    border-color: $ACCENT_BORDER;
    image: url($CHECK_IMAGE);
}
QCheckBox::indicator:disabled {
    background: $CHECK_DISABLED_BG;
    border-color: $BORDER;
}
QCheckBox:focus::indicator { border-color: $ACCENT; }
QProgressBar {
    background: $PROGRESS_BG;
    border: 1px solid $PROGRESS_BORDER;
    border-radius: 5px;
    min-height: 9px;
    max-height: 9px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 $PROGRESS_FROM, stop:1 $PROGRESS_TO);
}
QTableWidget {
    background: $TABLE_BG;
    alternate-background-color: $TABLE_ALT;
    gridline-color: $TABLE_GRID;
    border: 1px solid $BORDER;
    border-radius: 12px;
    selection-background-color: $TABLE_SELECTED;
    selection-color: $TABLE_SELECTED_TEXT;
    outline: 0;
}
QTableWidget::item { padding: 6px 8px; }
QTableWidget::item:hover { background: $TABLE_HOVER; }
QTableWidget::item:selected { background: $TABLE_SELECTED; color: $TABLE_SELECTED_TEXT; }
QTableWidget::item:focus { border: none; }
QHeaderView::section {
    background: $TABLE_HEADER_BG;
    color: $TABLE_HEADER_TEXT;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid $TABLE_GRID;
    border-bottom: 1px solid $TABLE_HEADER_BORDER;
    font-weight: 600;
    font-size: 8.5pt;
}
QTableCornerButton::section { background: $TABLE_HEADER_BG; border: none; }
QPlainTextEdit#LogView {
    background: $LOG_BG;
    border: 1px solid $LOG_BORDER;
    border-radius: 10px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 9pt;
    color: $LOG_TEXT;
    padding: 8px;
    selection-background-color: $LOG_SELECTION;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $SCROLL;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: $SCROLL_HOVER; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $SCROLL;
    border-radius: 4px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: $SCROLL_HOVER; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSlider::groove:horizontal {
    height: 4px;
    background: $SLIDER_GROOVE;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: $ACCENT_STRONG;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: $SLIDER_HANDLE;
    border: 2px solid $ACCENT_STRONG;
    width: 12px;
    height: 12px;
    margin: -6px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: $SLIDER_HANDLE_HOVER; }
QToolTip {
    background: $TOOLTIP_BG;
    color: $TOOLTIP_TEXT;
    border: 1px solid $TOOLTIP_BORDER;
    border-radius: 6px;
    padding: 5px 7px;
}
QMessageBox, QDialog { background: $DIALOG_BG; }
QMessageBox QLabel, QDialog QLabel { color: $DIALOG_TEXT; }
QMenu {
    background: $MENU_BG;
    border: 1px solid $MENU_BORDER;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item { padding: 5px 18px; border-radius: 6px; color: $TEXT; }
QMenu::item:selected { background: $MENU_SELECTED; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: $CAL_NAV; }
QCalendarWidget QToolButton { color: $TEXT; background: transparent; }
QCalendarWidget QAbstractItemView {
    background: $TABLE_BG;
    color: $TEXT;
    selection-background-color: $ACCENT_STRONG;
    selection-color: $PRIMARY_TEXT;
}
"""


def app_stylesheet(mode: str | None = None) -> str:
    chosen = theme.current_mode() if mode is None else mode
    if chosen not in theme.PALETTES:
        chosen = theme.DEFAULT_MODE
    tokens = dict(theme.PALETTES[chosen])
    tokens["CHEVRON_DOWN"] = (ICONS_DIR / tokens["CHEVRON_DOWN_FILE"]).as_posix()
    tokens["CHEVRON_UP"] = (ICONS_DIR / tokens["CHEVRON_UP_FILE"]).as_posix()
    tokens["CHECK_IMAGE"] = (ICONS_DIR / "check-white.svg").as_posix()
    return Template(_QSS).substitute(tokens)
