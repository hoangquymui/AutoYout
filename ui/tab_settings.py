"""Settings tab: General options, system paths, and API & AI services."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.request
import urllib.error
import zipfile

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.settings import ROOT, default_h2mtts_path
from ui import theme
from ui.components.icons import icon, set_icon
from ui.components.page import PageHeader, icon_button, paint_status, wrap_scroll
from utils.logger import get_logger

logger = get_logger("ui.settings")

# ── Google Gemini Models matching Google AI Studio Rate Limit ────────────────
GEMINI_TEXT_MODELS: List[Tuple[str, str]] = [
    ("Gemini 3.1 Flash Lite (Khuyên dùng · 15 RPM)", "gemini-3.1-flash-lite"),
    ("Gemini 2.5 Flash (Tiêu chuẩn)", "gemini-2.5-flash"),
    ("Gemini 2.5 Flash Lite (Siêu nhẹ)", "gemini-2.5-flash-lite"),
    ("Gemini 2.5 Pro (Tư duy sâu)", "gemini-2.5-pro"),
    ("Gemini 3 Flash", "gemini-3-flash"),
    ("Gemini 3.1 Pro", "gemini-3.1-pro"),
    ("Gemini 3.5 Flash Lite (15 RPM / 500 RPD)", "gemini-3.5-flash-lite"),
    ("Gemini 3.6 Flash", "gemini-3.6-flash"),
    ("Gemini 3.8 Flash", "gemini-3.8-flash"),
    ("Gemini 4 Argon (Thế hệ 4)", "gemini-4-argon"),
    ("Gemini 2 Flash", "gemini-2.0-flash"),
    ("Gemini 2 Flash Lite", "gemini-2.0-flash-lite"),
    ("Gemini Omni 1.1 Flash (Đa phương thức)", "gemini-omni-1.1-flash"),
    ("Gemma 4 26B (Open Model)", "gemma-4-26b"),
    ("Gemma 4 31B (Open Model)", "gemma-4-31b"),
]

GEMINI_TTS_MODELS: List[Tuple[str, str]] = [
    ("Gemini 2.5 Flash TTS (Tiêu chuẩn)", "gemini-2.5-flash-tts"),
    ("Gemini 2.5 Pro TTS (Chất lượng cao)", "gemini-2.5-pro-tts"),
    ("Gemini 3.1 Flash TTS (Thế hệ 3.1)", "gemini-3.1-flash-tts"),
    ("Gemini 3.8 Flash TTS (Thế hệ 3.8)", "gemini-3.8-flash-tts"),
    ("Gemini 3.8 Flash Lite TTS (Bản nhẹ)", "gemini-3.8-flash-lite-tts"),
    ("Gemini 2.5 Flash Native Audio Dialog (Live API)", "gemini-2.5-flash-native-audio-dialog"),
    ("Gemini 3 Flash Live (Live API)", "gemini-3-flash-live"),
    ("Gemini 3.5 Live Translate (Dịch trực tiếp)", "gemini-3.5-live-translate"),
    ("Gemini 3.5 Transcribe Live (Chuyển lời nói)", "gemini-3.5-transcribe-live"),
    ("Gemini 3.8 Live (Live API)", "gemini-3.8-live"),
]

GEMINI_IMAGE_MODELS: List[Tuple[str, str]] = [
    ("Nano Banana 2 (Gemini 3.1 Flash Image)", "nano-banana-2"),
    ("Nano Banana 2 Lite (Gemini 3.1 Flash Lite Image)", "nano-banana-2-lite"),
    ("Nano Banana (Gemini 2.5 Flash Preview Image)", "nano-banana"),
    ("Nano Banana Pro (Gemini 3 Pro Image)", "nano-banana-pro"),
    ("Gemini Nano Banana 2.1", "gemini-nano-banana-2.1"),
    ("Imagen 3.0 (Google Imagen 3)", "imagen-3.0-generate-002"),
    ("Imagen 4.0 (Google Imagen 4)", "imagen-4.0-generate-001"),
]

VEO_VIDEO_MODELS: List[Tuple[str, str]] = [
    ("Veo 3 Fast Generate (Tạo nhanh)", "veo-3-fast-generate"),
    ("Veo 3 Generate (Chuẩn chất lượng)", "veo-3-generate"),
    ("Veo 3 Lite Generate (Tiết kiệm)", "veo-3-lite-generate"),
    ("Veo 3.1 Lite (Rẻ nhất)", "veo-3.1-lite"),
]


def detect_hardware(h2mtts_path: str = "") -> Dict[str, Any]:
    """Detect local CPU, RAM, GPU, free disk space and H2MTTS installation."""
    specs: Dict[str, Any] = {
        "cpu": "",
        "ram": "",
        "disk": "",
        "gpu": "",
        "rating": "Khá - bản CPU",
        "note": "",
        "installed": False,
    }

    # 1. CPU
    cpu_name = ""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        cpu_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        cpu_name = str(cpu_name).strip()
    except Exception:
        cpu_name = platform.processor() or "CPU"
    cores = os.cpu_count() or 1
    specs["cpu"] = f"{cpu_name} · {cores} luồng"

    # 2. RAM
    try:
        import psutil
        total_ram = round(psutil.virtual_memory().total / (1024**3))
        specs["ram"] = f"{total_ram} GB"
    except Exception:
        specs["ram"] = "16 GB"

    # 3. Free disk
    target_drive = "D:/" if os.path.exists("D:/") else "C:/"
    if h2mtts_path and Path(h2mtts_path).drive:
        target_drive = Path(h2mtts_path).drive + "/"
    try:
        _, _, free = shutil.disk_usage(target_drive)
        specs["disk"] = f"{round(free / (1024**3))} GB"
    except Exception:
        specs["disk"] = "40 GB"

    # 4. GPU & Driver
    gpu_name = ""
    gpu_vram = 0.0
    driver = ""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2,
        )
        parts = [p.strip() for p in out.strip().split(",")]
        if len(parts) >= 3:
            gpu_name = parts[0]
            gpu_vram = round(float(parts[1]) / 1024, 1)
            driver = parts[2]
    except Exception:
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_vram = round(
                    torch.cuda.get_device_properties(0).total_memory / (1024**3), 1
                )
        except Exception:
            pass

    if gpu_name:
        specs["gpu"] = (
            f"{gpu_name} · {gpu_vram} GB · driver {driver}"
            if driver
            else f"{gpu_name} · {gpu_vram} GB"
        )
        specs["rating"] = "TỐT - bản GPU"
        specs["note"] = (
            f"• GPU {gpu_name} ({gpu_vram} GB VRAM) - đọc nhanh hơn thời gian thực nhiều lần"
        )
    else:
        specs["gpu"] = "Chỉ có CPU (Không phát hiện GPU NVIDIA)"
        specs["rating"] = "Khá - bản CPU"
        specs["note"] = "• Chạy bằng CPU - khuyến nghị dùng GPU NVIDIA để tăng tốc đọc"

    # 5. H2MTTS directory check
    raw_path = h2mtts_path.strip() if h2mtts_path else default_h2mtts_path()
    check_dir = Path(raw_path)
    if not check_dir.is_absolute():
        check_dir = (ROOT / check_dir).resolve()
    specs["installed"] = check_dir.exists() and (
        (check_dir / "models").exists() or (check_dir / "modules").exists()
    )
    return specs


class ToggleSwitch(QAbstractButton):
    """Modern iOS/Tailwind-style pill toggle switch."""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        if self.isChecked():
            bg_color = QColor("#059669")
        else:
            bg_color = (
                QColor("#475569")
                if theme.current_mode() == "dark"
                else QColor("#CBD5E1")
            )

        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 11, 11)

        thumb_color = QColor("#FFFFFF")
        painter.setBrush(thumb_color)
        thumb_x = rect.width() - 19 if self.isChecked() else 3
        painter.drawEllipse(thumb_x, 3, 16, 16)


class _CheckApiWorker(QThread):
    done = Signal(str, bool, str)
    models_discovered = Signal(str, list)

    def __init__(
        self,
        service_id: str,
        api_key: str,
        endpoint: str = "",
        model: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service_id = service_id
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        self.model = model.strip()

    def run(self) -> None:
        sid = self.service_id
        key = self.api_key

        if sid in ("gemini", "gemini_tts", "gemini_image", "veo"):
            if not key:
                self.done.emit(sid, False, "Chưa nhập API key Gemini.")
                return
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                req = urllib.request.Request(url, headers={"User-Agent": "AutoYout/1.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        raw = resp.read().decode("utf-8")
                        discovered: List[Tuple[str, str]] = []
                        try:
                            data = json.loads(raw)
                            for m in data.get("models", []):
                                raw_name = m.get("name", "")
                                m_id = raw_name.replace("models/", "").strip()
                                display_name = m.get("displayName", m_id)
                                methods = m.get("supportedGenerationMethods", [])
                                if sid == "gemini" and "generateContent" in methods:
                                    discovered.append((display_name, m_id))
                                elif sid == "gemini_tts" and any("audio" in m_id.lower() or "tts" in m_id.lower() or "speech" in str(methods).lower()):
                                    discovered.append((display_name, m_id))
                                elif sid == "gemini_image" and any("imagen" in m_id.lower() or "image" in m_id.lower() or "banana" in m_id.lower()):
                                    discovered.append((display_name, m_id))
                                elif sid == "veo" and "veo" in m_id.lower():
                                    discovered.append((display_name, m_id))
                        except Exception:
                            pass
                        msg = f"Kết nối Gemini thành công! ({len(discovered)} models khả dụng)" if discovered else "Kết nối Gemini thành công!"
                        if discovered:
                            self.models_discovered.emit(sid, discovered)
                        self.done.emit(sid, True, msg)
                        return
            except urllib.error.HTTPError as exc:
                self.done.emit(sid, False, f"Lỗi HTTP {exc.code} (Key không hợp lệ)")
                return
            except Exception as exc:
                self.done.emit(sid, False, f"Lỗi kết nối: {exc}")
                return

        elif sid == "edge_tts":
            self.done.emit(sid, True, "Edge-TTS sẵn sàng hoạt động (Miễn phí).")
            return

        self.done.emit(sid, True, "Key hợp lệ.")


class ServiceCard(QFrame):
    """Reusable card matching the user's reference software layout."""

    changed = Signal()
    save_requested = Signal(str, str)  # service_id, key

    def __init__(
        self,
        service_id: str,
        title: str,
        tag: str = "",
        subtitle: str = "",
        key_placeholder: str = "",
        models: Optional[List[Tuple[str, str]]] = None,
        price_note: str = "",
        shared_from: str = "",
        has_endpoint: bool = False,
        has_test_model: bool = False,
        has_key_input: bool = True,
        key_provider: Optional[Callable[[], str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service_id = service_id
        self.title_text = title
        self.tag_text = tag
        self.shared_from = shared_from
        self.has_key_input = has_key_input
        self.has_endpoint = has_endpoint
        self.key_provider = key_provider
        self._checker: Optional[_CheckApiWorker] = None

        self.setObjectName("ServiceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # ── 1. Top row: Title + Tag + Status + Toggle ────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_lbl = QLabel(title)
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(10)
        title_lbl.setFont(title_font)
        top_row.addWidget(title_lbl)

        if tag:
            tag_lbl = QLabel(tag)
            tag_lbl.setStyleSheet(
                "background: rgba(148, 163, 184, 0.2); color: #64748B; "
                "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
            )
            top_row.addWidget(tag_lbl)

        top_row.addStretch(1)

        self.status_badge = QLabel("• Chưa kích hoạt / Thiếu key")
        self.status_badge.setStyleSheet(
            "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
            "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
        )
        top_row.addWidget(self.status_badge)

        self.switch = ToggleSwitch(checked=False)
        self.switch.toggled.connect(lambda _: self.changed.emit())
        top_row.addWidget(self.switch)
        layout.addLayout(top_row)

        # ── 2. Subtitle / Shared key info ────────────────────────────────────
        self.sub_lbl = QLabel()
        self.sub_lbl.setWordWrap(True)
        self.sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        if shared_from:
            self.sub_lbl.setText(
                f"Dùng chung API key {shared_from}\n"
                f"↔ Dùng chung API key {shared_from} – chưa có key. Dùng key riêng"
            )
        elif subtitle:
            self.sub_lbl.setText(subtitle)
        layout.addWidget(self.sub_lbl)

        # ── 3. Key input row & Model combo ───────────────────────────────────
        mid_row = QHBoxLayout()
        mid_row.setSpacing(6)

        if has_key_input:
            self.key_edit = QLineEdit()
            self.key_edit.setPlaceholderText(key_placeholder or "Nhập API key...")
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.key_edit.textChanged.connect(self._on_key_changed)
            mid_row.addWidget(self.key_edit, 2)

            self.eye_btn = icon_button(
                "eye", "Hiện/Ẩn API key", role="muted", size=14, side=30
            )
            self.eye_btn.clicked.connect(self._toggle_eye)
            mid_row.addWidget(self.eye_btn)

        if models:
            self.model_combo = QComboBox()
            self.model_combo.setEditable(True)
            self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            for label, val in models:
                self.model_combo.addItem(label, val)
            self.model_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
            self.model_combo.currentTextChanged.connect(lambda _: self.changed.emit())
            mid_row.addWidget(self.model_combo, 1)

        if has_key_input or models:
            layout.addLayout(mid_row)

        # ── 4. Collapsible Endpoint ──────────────────────────────────────────
        if has_endpoint:
            ep_toggle_row = QHBoxLayout()
            self.ep_toggle_btn = QPushButton("v Endpoint tùy chỉnh")
            self.ep_toggle_btn.setFlat(True)
            self.ep_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.ep_toggle_btn.setStyleSheet(
                "color: #0284C7; text-align: left; font-size: 11px; padding: 0px;"
            )
            self.ep_toggle_btn.clicked.connect(self._toggle_endpoint)
            ep_toggle_row.addWidget(self.ep_toggle_btn)
            ep_toggle_row.addStretch(1)
            layout.addLayout(ep_toggle_row)

            self.ep_container = QWidget()
            ep_layout = QHBoxLayout(self.ep_container)
            ep_layout.setContentsMargins(0, 0, 0, 0)
            ep_layout.setSpacing(6)
            self.ep_edit = QLineEdit()
            self.ep_edit.setPlaceholderText("https://gateway.noi-bo/v1 (để trống = mặc định)")
            ep_save = QPushButton("Lưu")
            ep_save.setFixedWidth(50)
            ep_save.clicked.connect(lambda: self.changed.emit())
            ep_layout.addWidget(self.ep_edit, 1)
            ep_layout.addWidget(ep_save)
            self.ep_container.setVisible(False)
            layout.addWidget(self.ep_container)

        # ── 5. Price note ────────────────────────────────────────────────────
        if price_note:
            price_lbl = QLabel(price_note)
            price_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
            layout.addWidget(price_lbl)

        # ── 6. Bottom action buttons ─────────────────────────────────────────
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        if has_key_input:
            self.btn_save = QPushButton("Lưu key")
            set_icon(self.btn_save, "key", "muted", 12)
            self.btn_save.clicked.connect(self._save_clicked)
            act_row.addWidget(self.btn_save)

        if has_test_model:
            self.btn_test = QPushButton("Thử model")
            set_icon(self.btn_test, "play", "muted", 12)
            self.btn_test.clicked.connect(self._test_model_clicked)
            act_row.addWidget(self.btn_test)

        self.btn_check = QPushButton("Check Connection")
        set_icon(self.btn_check, "check-circle", "accent", 12)
        self.btn_check.clicked.connect(self.check_connection)
        act_row.addWidget(self.btn_check)

        act_row.addStretch(1)
        layout.addLayout(act_row)

        self.apply_theme()

    def get_key(self) -> str:
        if hasattr(self, "key_edit") and self.key_edit.text().strip():
            return self.key_edit.text().strip()
        if self.key_provider:
            try:
                return self.key_provider().strip()
            except Exception:
                return ""
        return ""

    def get_model(self) -> str:
        if not hasattr(self, "model_combo"):
            return ""
        cur_idx = self.model_combo.currentIndex()
        cur_text = self.model_combo.currentText().strip()
        if cur_idx >= 0 and self.model_combo.itemText(cur_idx) == cur_text:
            data = self.model_combo.itemData(cur_idx)
            if data:
                return str(data)
        return cur_text

    def set_model(self, model_name: str) -> None:
        if not hasattr(self, "model_combo") or not model_name:
            return
        idx = self.model_combo.findData(model_name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
            return
        idx = self.model_combo.findText(model_name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
            return
        self.model_combo.setEditText(model_name)

    def _toggle_eye(self) -> None:
        if not hasattr(self, "key_edit"):
            return
        if self.key_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            set_icon(self.eye_btn, "eye", "accent", 14)
        else:
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            set_icon(self.eye_btn, "eye", "muted", 14)

    def _toggle_endpoint(self) -> None:
        if not hasattr(self, "ep_container"):
            return
        is_hidden = self.ep_container.isHidden()
        self.ep_container.setVisible(is_hidden)
        self.ep_toggle_btn.setText(
            "^ Endpoint tùy chỉnh" if is_hidden else "v Endpoint tùy chỉnh"
        )

    def _on_key_changed(self, text: str) -> None:
        if text.strip():
            self.set_status("active", "• Đã nhập key")
        elif self.shared_from:
            self.set_status("needed", f"• Cần key {self.shared_from}")
        else:
            self.set_status("missing", "• Chưa kích hoạt / Thiếu key")
        self.changed.emit()

    def _save_clicked(self) -> None:
        key = self.get_key()
        self.save_requested.emit(self.service_id, key)
        self.set_status("valid", "• Đã lưu key")

    def _test_model_clicked(self) -> None:
        self.check_connection()

    def check_connection(self) -> None:
        if self._checker is not None:
            return
        key = self.get_key()
        ep = self.ep_edit.text().strip() if hasattr(self, "ep_edit") else ""
        model = self.get_model()

        if not key and self.service_id != "edge_tts":
            if self.shared_from:
                self.set_status("needed", f"• Cần key {self.shared_from}")
                self.btn_check.setToolTip(f"Vui lòng nhập API key ở ô {self.shared_from} trước khi kiểm tra.")
            else:
                self.set_status("missing", "• Chưa nhập API key")
                self.btn_check.setToolTip("Vui lòng nhập API key.")
            return

        self.set_status("running", "• Đang kiểm tra…")
        self._checker = _CheckApiWorker(self.service_id, key, ep, str(model or ""), self)
        self._checker.done.connect(self._on_check_done)
        self._checker.models_discovered.connect(self._on_models_discovered)
        self._checker.start()

    def _on_models_discovered(self, _sid: str, models: List[Tuple[str, str]]) -> None:
        if not hasattr(self, "model_combo") or not models:
            return
        existing_values = {
            self.model_combo.itemData(i) for i in range(self.model_combo.count())
        }
        for label, val in models:
            if val and val not in existing_values:
                self.model_combo.addItem(f"{label} ({val})", val)
                existing_values.add(val)

    def _on_check_done(self, _sid: str, success: bool, message: str) -> None:
        self._checker = None
        if success:
            if self.shared_from and not (hasattr(self, "key_edit") and self.key_edit.text().strip()):
                self.set_status("valid", f"• Hợp lệ (Dùng chung {self.shared_from})")
            else:
                self.set_status("valid", "• Hợp lệ")
            self.btn_check.setToolTip(message)
        else:
            self.set_status("missing", f"• {message}")
            self.btn_check.setToolTip(message)

    def set_status(self, kind: str, text: str) -> None:
        self.status_badge.setText(text)
        if kind == "valid":
            self.status_badge.setStyleSheet(
                "background: rgba(16, 185, 129, 0.18); color: #10B981; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        elif kind == "needed":
            self.status_badge.setStyleSheet(
                "background: rgba(245, 158, 11, 0.18); color: #F59E0B; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        elif kind == "running":
            self.status_badge.setStyleSheet(
                "background: rgba(2, 132, 199, 0.18); color: #0284C7; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        else:
            self.status_badge.setStyleSheet(
                "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )

    def update_shared_status(self, has_parent_key: bool) -> None:
        if not self.shared_from:
            return
        has_own = bool(
            hasattr(self, "key_edit") and self.key_edit.text().strip()
        )
        if has_own:
            self.set_status("valid", "• Hợp lệ (Key riêng)")
            self.sub_lbl.setText(f"Đang dùng API key riêng cho {self.title_text}.")
        elif has_parent_key:
            self.set_status("valid", f"• Hợp lệ (Dùng chung {self.shared_from})")
            self.sub_lbl.setText(
                f"Dùng chung API key {self.shared_from} · Sẵn sàng hoạt động."
            )
        else:
            self.set_status("needed", f"• Cần key {self.shared_from}")
            self.sub_lbl.setText(
                f"Dùng chung API key {self.shared_from}\n"
                f"↔ Dùng chung API key {self.shared_from} – chưa có key. Dùng key riêng"
            )

    def apply_theme(self) -> None:
        dark = theme.current_mode() == "dark"
        bg = "#111C2D" if dark else "#FFFFFF"
        border = "#2A3E59" if dark else "#E2E8F0"
        self.setStyleSheet(
            f"QFrame#ServiceCard {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
        )


# ── H2MTTS Hugging Face Packages (https://huggingface.co/hoangmui410) ─────────
H2M_PACKAGES: List[Dict[str, Any]] = [
    {
        "id": "turbo",
        "name": "H2M-TTS v3 Turbo",
        "tag": "Khuyên dùng · GPU & CPU",
        "tag_color": "#0284C7",
        "tag_bg": "rgba(2, 132, 199, 0.15)",
        "repo": "hoangmui410/H2M-TTS-v3-Turbo",
        "size": "~295 MB",
        "desc": "Model chính đọc tiếng Việt tự nhiên (GGUF Q8, ONNX-Int8, G2P, Speaker Encoder, Denoiser).",
        "target_subpath": "models/H2M-TTS-v3-Turbo",
        "check_files": ["vieneu-v3-turbo-q8_0.gguf", "sea_g2p.bin", "denoiser.onnx", "onnx_int8", "update"],
        "core": True,
    },
    {
        "id": "moss",
        "name": "MOSS Audio Tokenizer Nano",
        "tag": "Bắt buộc cho Turbo",
        "tag_color": "#D97706",
        "tag_bg": "rgba(217, 119, 6, 0.15)",
        "repo": "hoangmui410/MOSS-Audio-Tokenizer-Nano",
        "size": "~85 MB",
        "desc": "Audio Codec giải mã nơ-ron chất lượng 48kHz bắt buộc cho dòng Turbo.",
        "target_subpath": "models/MOSS-Audio-Tokenizer-Nano",
        "check_files": ["config.json", "model-00001-of-00001.safetensors"],
        "core": True,
    },
    {
        "id": "voice_packs",
        "name": "H2M Voice Packs",
        "tag": "25+ Giọng Việt",
        "tag_color": "#059669",
        "tag_bg": "rgba(5, 150, 105, 0.15)",
        "repo": "hoangmui410/H2M-Voice-Packs",
        "size": "~5 MB",
        "desc": "Danh mục 25+ giọng đọc Bắc / Trung / Nam, preset cảm xúc và file embeddings.",
        "target_subpath": "voice-packs",
        "check_files": ["voice-catalog-v4.json", "voice-pack.json"],
        "core": True,
    },
    {
        "id": "nano",
        "name": "H2M-TTS v3 Nano",
        "tag": "Bản nhẹ · Tối ưu CPU",
        "tag_color": "#64748B",
        "tag_bg": "rgba(100, 116, 139, 0.15)",
        "repo": "hoangmui410/H2M-TTS-v3-Nano",
        "size": "~268 MB",
        "desc": "Model ONNX Flow siêu nhẹ, chạy ổn định trên CPU máy văn phòng.",
        "target_subpath": "models/H2M-TTS-v3-Nano",
        "check_files": ["codec_decoder.onnx", "vector_estimator.onnx", "config.json"],
        "core": False,
    },
    {
        "id": "runtime",
        "name": "Engine Runtime Windows",
        "tag": "Tăng tốc GPU/DirectML",
        "tag_color": "#7C3AED",
        "tag_bg": "rgba(124, 58, 237, 0.15)",
        "repo": "hoangmui410/runtime",
        "size": "~89 MB",
        "desc": "Thư viện C++ DirectML & ONNX Runtime tăng tốc GPU phần cứng trên Windows.",
        "target_subpath": "engine-runtimes",
        "check_files": ["DirectML.dll", "onnxruntime.dll", "runtime.zip"],
        "core": False,
        "is_zip": True,
    },
]


class H2MDownloadWorker(QThread):
    progress = Signal(int, str)  # percent, text
    pkg_status_changed = Signal(str, str, str)  # pkg_id, status_kind, text
    all_finished = Signal(bool, str)

    def __init__(
        self,
        target_root: str,
        packages: List[Dict[str, Any]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.target_root = Path(target_root)
        self.packages = packages
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        from huggingface_hub import snapshot_download

        total_pkgs = len(self.packages)
        success_count = 0

        for idx, pkg in enumerate(self.packages):
            if self._is_cancelled:
                self.all_finished.emit(False, "Đã hủy tiến trình tải.")
                return

            pkg_id = pkg["id"]
            repo_id = pkg["repo"]
            target_dir = self.target_root / pkg["target_subpath"]
            target_dir.mkdir(parents=True, exist_ok=True)

            current_pct = int((idx / total_pkgs) * 100)
            self.progress.emit(
                current_pct,
                f"Đang tải {pkg['name']} ({idx + 1}/{total_pkgs}) từ Hugging Face…",
            )
            self.pkg_status_changed.emit(pkg_id, "downloading", "Đang tải…")

            try:
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(target_dir),
                    repo_type="model",
                )

                if pkg.get("is_zip"):
                    zip_file = target_dir / "runtime.zip"
                    if zip_file.exists():
                        self.progress.emit(
                            current_pct,
                            f"Đang giải nén runtime vào {target_dir.name}…",
                        )
                        try:
                            with zipfile.ZipFile(zip_file, "r") as zf:
                                zf.extractall(target_dir)
                        except Exception as exc:
                            logger.warning("Lỗi giải nén runtime.zip: %s", exc)

                success_count += 1
                self.pkg_status_changed.emit(pkg_id, "installed", "✓ Đã có")
            except Exception as exc:
                logger.error("Lỗi khi tải %s: %s", repo_id, exc)
                self.pkg_status_changed.emit(pkg_id, "error", f"Lỗi: {exc}")

        self.progress.emit(100, "Hoàn tất quá trình tải.")
        all_ok = success_count == total_pkgs
        msg = (
            f"Đã tải thành công {success_count}/{total_pkgs} gói tài nguyên."
            if all_ok
            else f"Tải xong {success_count}/{total_pkgs} gói (có lỗi xảy ra)."
        )
        self.all_finished.emit(all_ok, msg)


class H2MTTSCard(QFrame):
    """Local H2MTTS engine, hardware specifications detector, and Hugging Face downloader."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("H2MTTSCard")
        self._downloader: Optional[H2MDownloadWorker] = None
        self.pkg_widgets: Dict[str, Dict[str, Any]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # ── 1. Header ────────────────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_lbl = QLabel("Giọng đọc local – H2MTTS")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(10)
        title_lbl.setFont(title_font)
        top_row.addWidget(title_lbl)

        tag_lbl = QLabel("Nâng cao")
        tag_lbl.setStyleSheet(
            "background: rgba(148, 163, 184, 0.2); color: #64748B; "
            "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
        )
        top_row.addWidget(tag_lbl)
        top_row.addStretch(1)

        self.status_badge = QLabel("Chưa cài")
        top_row.addWidget(self.status_badge)
        layout.addLayout(top_row)

        desc = QLabel(
            "Chạy model giọng tiếng Việt ngay trên máy: miễn phí, không giới hạn ký tự, "
            "25 giọng có sẵn (Bắc / Trung / Nam) và clone giọng từ 3–15 giây giọng mẫu. "
            "Mã nguồn mở Apache 2.0 – audio dùng thương mại được."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(desc)

        # ── 2. Hardware Specifications Box ───────────────────────────────────
        self.specs_box = QFrame()
        self.specs_box.setObjectName("SpecsBox")
        specs_layout = QVBoxLayout(self.specs_box)
        specs_layout.setContentsMargins(12, 10, 12, 10)
        specs_layout.setSpacing(8)

        rate_row = QHBoxLayout()
        self.rating_badge = QLabel("• Đánh giá cấu hình: TỐT - bản GPU")
        self.rating_badge.setStyleSheet(
            "background: rgba(16, 185, 129, 0.18); color: #10B981; "
            "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
        )
        rate_row.addWidget(self.rating_badge)
        rate_row.addStretch(1)

        self.btn_redetect = icon_button(
            "refresh", "Dò lại cấu hình phần cứng", role="muted", size=13, side=28
        )
        self.btn_redetect.clicked.connect(self.detect_specs)
        btn_redetect_text = QPushButton("Dò lại")
        btn_redetect_text.setFlat(True)
        btn_redetect_text.setStyleSheet("color: #0284C7; font-size: 11px;")
        btn_redetect_text.clicked.connect(self.detect_specs)
        rate_row.addWidget(self.btn_redetect)
        rate_row.addWidget(btn_redetect_text)
        specs_layout.addLayout(rate_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.addWidget(self._make_spec_label("CPU"), 0, 0)
        self.lbl_cpu = QLabel("—")
        grid.addWidget(self.lbl_cpu, 0, 1)

        grid.addWidget(self._make_spec_label("RAM"), 1, 0)
        self.lbl_ram = QLabel("—")
        grid.addWidget(self.lbl_ram, 1, 1)

        grid.addWidget(self._make_spec_label("GPU"), 2, 0)
        self.lbl_gpu = QLabel("—")
        grid.addWidget(self.lbl_gpu, 2, 1)

        grid.addWidget(self._make_spec_label("Ổ đĩa trống"), 3, 0)
        self.lbl_disk = QLabel("—")
        grid.addWidget(self.lbl_disk, 3, 1)
        specs_layout.addLayout(grid)

        self.lbl_note = QLabel("• GPU ... - đọc nhanh hơn thời gian thực nhiều lần")
        self.lbl_note.setStyleSheet("color: #64748B; font-size: 11px;")
        specs_layout.addWidget(self.lbl_note)
        layout.addWidget(self.specs_box)

        # ── 3. Path row ──────────────────────────────────────────────────────
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_lbl = QLabel("Thư mục H2MTTS:")
        path_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.path_edit = QLineEdit(default_h2mtts_path())
        self.path_edit.textChanged.connect(self._on_path_changed)
        btn_browse = icon_button(
            "folder-open", "Chọn thư mục H2MTTS…", role="muted", size=14, side=30
        )
        btn_browse.clicked.connect(self._browse_dir)

        path_row.addWidget(path_lbl)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(btn_browse)
        layout.addLayout(path_row)

        # ── 4. Hugging Face Download Manager Box ─────────────────────────────
        self.downloads_box = QFrame()
        self.downloads_box.setObjectName("DownloadsBox")
        dl_layout = QVBoxLayout(self.downloads_box)
        dl_layout.setContentsMargins(12, 12, 12, 12)
        dl_layout.setSpacing(10)

        # Header of downloads box
        dl_header = QHBoxLayout()
        dl_title = QLabel("📦 Tải mô hình từ Hugging Face (@hoangmui410)")
        dl_title_font = QFont()
        dl_title_font.setBold(True)
        dl_title_font.setPointSize(10)
        dl_title.setFont(dl_title_font)
        dl_header.addWidget(dl_title)
        dl_header.addStretch(1)

        btn_hf_link = QPushButton("Mở Hugging Face ↗")
        btn_hf_link.setFlat(True)
        btn_hf_link.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_hf_link.setStyleSheet("color: #0284C7; font-size: 11px; text-decoration: underline;")
        btn_hf_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/hoangmui410")))
        dl_header.addWidget(btn_hf_link)
        dl_layout.addLayout(dl_header)

        dl_desc = QLabel(
            "Tự động tải các model và voice-packs H2MTTS từ https://huggingface.co/hoangmui410 về thư mục trên máy để chạy offline."
        )
        dl_desc.setWordWrap(True)
        dl_desc.setStyleSheet("color: #64748B; font-size: 11px;")
        dl_layout.addWidget(dl_desc)

        # Quick action buttons bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_install = QPushButton("🚀 Tải trọn bộ khuyên dùng (~385 MB)")
        self.btn_install.setObjectName("primaryButton")
        set_icon(self.btn_install, "download", "white", 13)
        self.btn_install.clicked.connect(self._download_recommended_clicked)
        btn_bar.addWidget(self.btn_install)

        self.btn_dl_all = QPushButton("📦 Tải tất cả gói (~742 MB)")
        set_icon(self.btn_dl_all, "download", "accent", 13)
        self.btn_dl_all.clicked.connect(self._download_all_clicked)
        btn_bar.addWidget(self.btn_dl_all)

        self.btn_check_engine = QPushButton("🔄 Quét lại")
        set_icon(self.btn_check_engine, "check-circle", "muted", 12)
        self.btn_check_engine.clicked.connect(self.check_packages_status)
        btn_bar.addWidget(self.btn_check_engine)

        btn_bar.addStretch(1)
        dl_layout.addLayout(btn_bar)

        # Progress panel (hidden by default)
        self.progress_container = QWidget()
        prog_layout = QVBoxLayout(self.progress_container)
        prog_layout.setContentsMargins(0, 4, 0, 4)
        prog_layout.setSpacing(4)

        prog_top = QHBoxLayout()
        self.lbl_progress = QLabel("Đang chuẩn bị tải…")
        self.lbl_progress.setStyleSheet("font-size: 11px; color: #0284C7; font-weight: bold;")
        prog_top.addWidget(self.lbl_progress, 1)

        self.btn_cancel_dl = QPushButton("Hủy")
        self.btn_cancel_dl.setFixedWidth(50)
        self.btn_cancel_dl.clicked.connect(self._cancel_download_clicked)
        prog_top.addWidget(self.btn_cancel_dl)
        prog_layout.addLayout(prog_top)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        prog_layout.addWidget(self.progress_bar)

        self.progress_container.setVisible(False)
        dl_layout.addWidget(self.progress_container)

        # Package rows list
        for pkg in H2M_PACKAGES:
            row_frame = QFrame()
            row_frame.setObjectName("PkgRow")
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(10)

            # Left column: Info
            info_col = QVBoxLayout()
            info_col.setSpacing(2)

            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            name_lbl = QLabel(pkg["name"])
            name_font = QFont()
            name_font.setBold(True)
            name_font.setPointSize(9)
            name_lbl.setFont(name_font)
            title_row.addWidget(name_lbl)

            tag_lbl = QLabel(pkg["tag"])
            tag_lbl.setStyleSheet(
                f"background: {pkg['tag_bg']}; color: {pkg['tag_color']}; "
                f"border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 600;"
            )
            title_row.addWidget(tag_lbl)
            title_row.addStretch(1)
            info_col.addLayout(title_row)

            sub_lbl = QLabel(f"{pkg['repo']} · {pkg['size']} — {pkg['desc']}")
            sub_lbl.setStyleSheet("color: #64748B; font-size: 10.5px;")
            sub_lbl.setWordWrap(True)
            info_col.addWidget(sub_lbl)
            row_layout.addLayout(info_col, 1)

            # Right column: Status badge & Action button
            status_badge = QLabel("Chưa kiểm tra")
            status_badge.setFixedWidth(80)
            status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addWidget(status_badge)

            btn_dl = QPushButton("Tải về")
            btn_dl.setFixedWidth(70)
            btn_dl.clicked.connect(lambda _, p=pkg: self._download_single_clicked(p))
            row_layout.addWidget(btn_dl)

            self.pkg_widgets[pkg["id"]] = {
                "frame": row_frame,
                "status_badge": status_badge,
                "btn_dl": btn_dl,
                "pkg": pkg,
            }
            dl_layout.addWidget(row_frame)

        layout.addWidget(self.downloads_box)

        # Status text line
        self.engine_status = QLabel("")
        layout.addWidget(self.engine_status)

        self.apply_theme()
        self.detect_specs()
        self.check_packages_status()

    @staticmethod
    def _make_spec_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #64748B; font-weight: bold; font-size: 11px;")
        lbl.setFixedWidth(80)
        return lbl

    def _browse_dir(self) -> None:
        start = self.path_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục H2MTTS", start)
        if chosen:
            self.path_edit.setText(chosen)
            self.detect_specs()
            self.check_packages_status()
            self.changed.emit()

    def _on_path_changed(self, _text: str) -> None:
        self.detect_specs()
        self.check_packages_status()
        self.changed.emit()

    def check_packages_status(self) -> None:
        path = Path(self.path_edit.text().strip())
        all_core_ok = True
        has_any = False

        for pkg in H2M_PACKAGES:
            pkg_id = pkg["id"]
            w = self.pkg_widgets.get(pkg_id)
            if not w:
                continue
            is_installed = self._is_pkg_installed(path, pkg)
            if is_installed:
                has_any = True
                w["status_badge"].setText("✓ Đã có")
                w["status_badge"].setStyleSheet(
                    "background: rgba(16, 185, 129, 0.15); color: #10B981; "
                    "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                )
                w["btn_dl"].setText("Tải lại")
            else:
                if pkg.get("core"):
                    all_core_ok = False
                w["status_badge"].setText("Chưa tải")
                w["status_badge"].setStyleSheet(
                    "background: rgba(245, 158, 11, 0.15); color: #F59E0B; "
                    "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                )
                w["btn_dl"].setText("Tải về")

        if path.exists() and all_core_ok:
            self.status_badge.setText("Đã cài đủ")
            self.status_badge.setStyleSheet(
                "background: rgba(16, 185, 129, 0.18); color: #10B981; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Đã cài đủ gói khuyên dùng")
        elif path.exists() and has_any:
            self.status_badge.setText("Thiếu gói")
            self.status_badge.setStyleSheet(
                "background: rgba(245, 158, 11, 0.18); color: #F59E0B; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Tải trọn bộ khuyên dùng (~385 MB)")
        else:
            self.status_badge.setText("Chưa cài")
            self.status_badge.setStyleSheet(
                "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Tải trọn bộ khuyên dùng (~385 MB)")

    @staticmethod
    def _is_pkg_installed(root: Path, pkg: Dict[str, Any]) -> bool:
        if not root.exists():
            return False
        target = root / pkg["target_subpath"]
        if not target.exists():
            return False
        for f in pkg.get("check_files", []):
            if (target / f).exists():
                return True
        return False

    def _download_single_clicked(self, pkg: Dict[str, Any]) -> None:
        self._start_download([pkg])

    def _download_recommended_clicked(self) -> None:
        recommended = [p for p in H2M_PACKAGES if p.get("core")]
        self._start_download(recommended)

    def _download_all_clicked(self) -> None:
        self._start_download(H2M_PACKAGES)

    def _start_download(self, pkgs: List[Dict[str, Any]]) -> None:
        if self._downloader is not None and self._downloader.isRunning():
            return
        root_path = self.path_edit.text().strip()
        if not root_path:
            paint_status(self.engine_status, "warning")
            self.engine_status.setText("Vui lòng chọn thư mục cài đặt H2MTTS trước khi tải.")
            return

        self.progress_container.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_progress.setText("Đang khởi tạo kết nối Hugging Face…")
        self.btn_install.setEnabled(False)
        self.btn_dl_all.setEnabled(False)

        self._downloader = H2MDownloadWorker(root_path, pkgs, parent=self)
        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.pkg_status_changed.connect(self._on_pkg_status_changed)
        self._downloader.all_finished.connect(self._on_download_finished)
        self._downloader.start()

    def _on_download_progress(self, percent: int, text: str) -> None:
        self.progress_bar.setValue(percent)
        self.lbl_progress.setText(text)

    def _on_pkg_status_changed(self, pkg_id: str, status_kind: str, text: str) -> None:
        w = self.pkg_widgets.get(pkg_id)
        if not w:
            return
        w["status_badge"].setText(text)
        if status_kind == "downloading":
            w["status_badge"].setStyleSheet(
                "background: rgba(2, 132, 199, 0.15); color: #0284C7; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
        elif status_kind == "installed":
            w["status_badge"].setStyleSheet(
                "background: rgba(16, 185, 129, 0.15); color: #10B981; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
            w["btn_dl"].setText("Tải lại")

    def _on_download_finished(self, success: bool, message: str) -> None:
        self._downloader = None
        self.btn_install.setEnabled(True)
        self.btn_dl_all.setEnabled(True)
        self.lbl_progress.setText(message)
        if success:
            paint_status(self.engine_status, "success")
        else:
            paint_status(self.engine_status, "warning")
        self.engine_status.setText(message)
        self.check_packages_status()
        self.detect_specs()
        self.changed.emit()

    def _cancel_download_clicked(self) -> None:
        if self._downloader:
            self._downloader.cancel()
            self.lbl_progress.setText("Đang hủy tải…")

    def detect_specs(self) -> None:
        path = self.path_edit.text().strip()
        data = detect_hardware(path)

        self.lbl_cpu.setText(data["cpu"])
        self.lbl_ram.setText(data["ram"])
        self.lbl_gpu.setText(data["gpu"])
        self.lbl_disk.setText(data["disk"])
        self.lbl_note.setText(data["note"])

        self.rating_badge.setText(f"• Đánh giá cấu hình: {data['rating']}")

    def apply_theme(self) -> None:
        dark = theme.current_mode() == "dark"
        bg = "#111C2D" if dark else "#FFFFFF"
        border = "#2A3E59" if dark else "#E2E8F0"
        box_bg = "rgba(15, 23, 42, 0.4)" if dark else "rgba(241, 245, 249, 0.7)"
        row_bg = "rgba(30, 41, 59, 0.5)" if dark else "#FFFFFF"
        self.setStyleSheet(
            f"QFrame#H2MTTSCard {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
        )
        self.specs_box.setStyleSheet(
            f"QFrame#SpecsBox {{ background-color: {box_bg}; border: 1px solid {border}; border-radius: 6px; }}"
        )
        if hasattr(self, "downloads_box"):
            self.downloads_box.setStyleSheet(
                f"QFrame#DownloadsBox {{ background-color: {box_bg}; border: 1px solid {border}; border-radius: 6px; }}"
            )
        if hasattr(self, "pkg_widgets"):
            for w in self.pkg_widgets.values():
                w["frame"].setStyleSheet(
                    f"QFrame#PkgRow {{ background-color: {row_bg}; border: 1px solid {border}; border-radius: 6px; }}"
                )


class GeneralSettingsPage(QWidget):
    """General application settings & tool paths."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        layout.addWidget(
            PageHeader(
                "Cài đặt chung",
                "Quản lý đường dẫn các công cụ nền tảng và tùy chọn hệ thống AutoYout.",
            )
        )

        # Paths Group
        paths_box = QGroupBox("Đường dẫn hệ thống")
        paths_form = QFormLayout(paths_box)
        paths_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # H2MTTS path
        h2_row = QHBoxLayout()
        self.h2mtts_edit = QLineEdit(default_h2mtts_path())
        btn_h2 = icon_button("folder", "Chọn thư mục H2MTTS", role="muted", size=14, side=30)
        btn_h2.clicked.connect(lambda: self._browse_dir(self.h2mtts_edit, "Chọn thư mục H2MTTS"))
        h2_row.addWidget(self.h2mtts_edit, 1)
        h2_row.addWidget(btn_h2)
        paths_form.addRow("Thư mục H2MTTS", self._wrap(h2_row))

        # FFmpeg
        ff_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit("ffmpeg")
        btn_ff = icon_button("file", "Chọn file ffmpeg.exe", role="muted", size=14, side=30)
        btn_ff.clicked.connect(lambda: self._browse_file(self.ffmpeg_edit, "Chọn ffmpeg.exe", "Chương trình (*.exe);;Tất cả (*)"))
        ff_row.addWidget(self.ffmpeg_edit, 1)
        ff_row.addWidget(btn_ff)
        paths_form.addRow("FFmpeg", self._wrap(ff_row))

        # FFprobe
        ffp_row = QHBoxLayout()
        self.ffprobe_edit = QLineEdit("ffprobe")
        btn_ffp = icon_button("file", "Chọn file ffprobe.exe", role="muted", size=14, side=30)
        btn_ffp.clicked.connect(lambda: self._browse_file(self.ffprobe_edit, "Chọn ffprobe.exe", "Chương trình (*.exe);;Tất cả (*)"))
        ffp_row.addWidget(self.ffprobe_edit, 1)
        ffp_row.addWidget(btn_ffp)
        paths_form.addRow("FFprobe", self._wrap(ffp_row))

        # Chrome Profile
        chrome_row = QHBoxLayout()
        self.chrome_edit = QLineEdit()
        self.chrome_edit.setPlaceholderText("%LOCALAPPDATA%\\AutoYout\\chrome-profile (mặc định)")
        btn_chrome = icon_button("folder", "Chọn thư mục Chrome Profile", role="muted", size=14, side=30)
        btn_chrome.clicked.connect(lambda: self._browse_dir(self.chrome_edit, "Chọn Chrome Profile"))
        chrome_row.addWidget(self.chrome_edit, 1)
        chrome_row.addWidget(btn_chrome)
        paths_form.addRow("Chrome Profile", self._wrap(chrome_row))

        layout.addWidget(paths_box)

        # App Group
        app_box = QGroupBox("Tùy chọn ứng dụng")
        app_form = QFormLayout(app_box)
        app_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Giao diện tối (Dark Mode)", "dark")
        self.theme_combo.addItem("Giao diện sáng (Light Mode)", "light")
        app_form.addRow("Giao diện", self.theme_combo)

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 5)
        self.retry_spin.setValue(1)
        app_form.addRow("Thử lại khi lỗi", self.retry_spin)

        self.stop_error_check = QCheckBox("Dừng tiến trình pipeline khi xảy ra lỗi nghiêm trọng")
        app_form.addRow("", self.stop_error_check)

        self.log_dir_edit = QLineEdit("logs")
        app_form.addRow("Thư mục Logs", self.log_dir_edit)

        layout.addWidget(app_box)
        layout.addStretch(1)

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _browse_dir(self, edit: QLineEdit, title: str) -> None:
        start = edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, title, start)
        if chosen:
            edit.setText(chosen)
            self.changed.emit()

    def _browse_file(self, edit: QLineEdit, title: str, filter_str: str) -> None:
        start = edit.text().strip() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, title, start, filter_str)
        if chosen:
            edit.setText(chosen)
            self.changed.emit()


class SettingsTab(QWidget):
    """Main Settings tab with Cài đặt chung and API sub-pages."""

    settings_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._settings: Dict[str, Any] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.inner_tabs = QTabWidget(self)
        self.inner_tabs.setDocumentMode(True)
        root.addWidget(self.inner_tabs, 1)

        # Tab 1: Cài đặt chung
        self.general_page = GeneralSettingsPage(self)
        self.general_page.changed.connect(self._on_changed)
        self.inner_tabs.addTab(wrap_scroll(self.general_page), "Cài đặt chung")

        # Tab 2: API
        api_wrapper = QWidget()
        api_layout = QVBoxLayout(api_wrapper)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.setSpacing(0)

        self.api_tabs = QTabWidget(api_wrapper)
        self.api_tabs.setDocumentMode(True)
        api_layout.addWidget(self.api_tabs, 1)

        self._build_text_ai_page()
        self._build_tts_page()
        self._build_media_ai_page()

        self.inner_tabs.addTab(api_wrapper, "API")

    # ── Page 1: Văn bản AI (Image 1) ─────────────────────────────────────────
    def _build_text_ai_page(self) -> None:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "Văn bản AI",
                "Viết kịch bản, caption, trả lời tự động bằng Google Gemini.",
            )
        )

        self.card_gemini = ServiceCard(
            service_id="gemini",
            title="Gemini Flash",
            tag="Gemini Flash (key dùng chung cho Nano Banana, Veo, Lyria)",
            key_placeholder="Nhập AIza...",
            models=GEMINI_TEXT_MODELS,
            has_endpoint=True,
            has_test_model=True,
            parent=page,
        )
        self.card_gemini.changed.connect(self._sync_shared_keys)
        self.card_gemini.changed.connect(self._on_changed)
        layout.addWidget(self.card_gemini)

        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Văn bản AI")

    # ── Page 2: Giọng đọc (TTS) (Image 2) ────────────────────────────────────
    def _build_tts_page(self) -> None:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "Giọng đọc (TTS)",
                "Thuyết minh cho Reels & TikTok.",
            )
        )

        grid = QGridLayout()
        grid.setSpacing(12)

        # 1. Google Gemini TTS
        self.card_gemini_tts = ServiceCard(
            service_id="gemini_tts",
            title="Google Gemini TTS",
            shared_from="Gemini Flash",
            models=GEMINI_TTS_MODELS,
            price_note="Đơn giá: ≈ $20 / 1M ký tự (ước tính)",
            has_key_input=False,
            key_provider=self.card_gemini.get_key,
            parent=page,
        )
        self.card_gemini_tts.changed.connect(self._on_changed)
        grid.addWidget(self.card_gemini_tts, 0, 0)

        # 2. Edge-TTS (Miễn phí)
        self.card_edge_tts = ServiceCard(
            service_id="edge_tts",
            title="Edge-TTS (Miễn phí)",
            subtitle="Miễn phí – không cần API key\nKhông cần API key, không giới hạn ký tự. Giọng Việt HoaiMy & NamMinh Neural.",
            models=[
                ("NamMinh (Nam)", "vi-VN-NamMinhNeural"),
                ("HoaiMy (Nữ)", "vi-VN-HoaiMyNeural"),
            ],
            has_key_input=False,
            parent=page,
        )
        self.card_edge_tts.switch.setChecked(True)
        self.card_edge_tts.set_status("valid", "• Hợp lệ")
        self.card_edge_tts.changed.connect(self._on_changed)
        grid.addWidget(self.card_edge_tts, 0, 1)

        layout.addLayout(grid)

        # 5. Local H2MTTS (Nâng cao)
        self.card_h2mtts = H2MTTSCard(parent=page)
        self.card_h2mtts.changed.connect(self._on_changed)
        layout.addWidget(self.card_h2mtts)

        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Giọng đọc (TTS)")

    # ── Page 3: Ảnh / Video AI (Image 3) ─────────────────────────────────────
    def _build_media_ai_page(self) -> None:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "Ảnh / Video AI",
                "Tạo ảnh minh họa & video bằng Google Nano Banana, Imagen & Veo (dùng chung API key Gemini).",
            )
        )

        grid = QGridLayout()
        grid.setSpacing(12)

        # 1. Google Gemini - Ảnh AI
        self.card_gemini_image = ServiceCard(
            service_id="gemini_image",
            title="Google Gemini – Ảnh AI",
            shared_from="Gemini Flash",
            models=GEMINI_IMAGE_MODELS,
            price_note="Đơn giá: ≈ $0.039 / ảnh 1K (ước tính)",
            has_key_input=False,
            key_provider=self.card_gemini.get_key,
            parent=page,
        )
        self.card_gemini_image.switch.setChecked(True)
        self.card_gemini_image.changed.connect(self._on_changed)
        grid.addWidget(self.card_gemini_image, 0, 0)

        # 2. Google Veo 3 - Video AI
        self.card_veo = ServiceCard(
            service_id="veo",
            title="Google Veo 3 – Video AI",
            tag="Video AI",
            shared_from="Gemini Flash",
            models=VEO_VIDEO_MODELS,
            price_note="Đơn giá: $0.05 / giây (720p)",
            has_key_input=False,
            key_provider=self.card_gemini.get_key,
            parent=page,
        )
        self.card_veo.switch.setChecked(False)
        self.card_veo.changed.connect(self._on_changed)
        grid.addWidget(self.card_veo, 0, 1)

        layout.addLayout(grid)
        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Ảnh / Video AI")

    # ── Shared key propagation ───────────────────────────────────────────────
    def _sync_shared_keys(self) -> None:
        gemini_key = self.card_gemini.get_key()
        has_gemini = bool(gemini_key)
        self.card_gemini_tts.update_shared_status(has_gemini)
        self.card_gemini_image.update_shared_status(has_gemini)
        if hasattr(self, "card_veo"):
            self.card_veo.update_shared_status(has_gemini)

    def _on_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        self._sync_shared_keys()
        self.settings_changed.emit()

    # ── settings load & apply ────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._loading = True
        try:
            self._settings = settings
            paths = settings.get("paths") or {}
            app = settings.get("app") or {}
            script = settings.get("script") or {}
            video = settings.get("video") or {}
            api = settings.get("api") or {}

            # General paths
            raw_h2 = str(paths.get("h2mtts") or "").strip()
            if not raw_h2 or raw_h2.replace("\\", "/").lower() == "d:/mui/f375/tts/h2mtts":
                h2 = default_h2mtts_path()
            elif not Path(raw_h2).is_absolute():
                h2 = str((ROOT / raw_h2).resolve().as_posix())
            else:
                h2 = raw_h2
            self.general_page.h2mtts_edit.setText(h2)
            self.card_h2mtts.path_edit.setText(h2)
            self.card_h2mtts.detect_specs()

            self.general_page.ffmpeg_edit.setText(str(paths.get("ffmpeg") or "ffmpeg"))
            self.general_page.ffprobe_edit.setText(str(paths.get("ffprobe") or "ffprobe"))
            self.general_page.chrome_edit.setText(str(settings.get("youtube", {}).get("browser_profile") or ""))
            self.general_page.retry_spin.setValue(int(app.get("retry", 1) or 1))
            self.general_page.stop_error_check.setChecked(bool(app.get("stop_on_error", False)))
            self.general_page.log_dir_edit.setText(str(app.get("log_dir") or "logs"))

            # Gemini key sync from script/video
            gemini_val = str(api.get("gemini_key") or script.get("api_key") or video.get("ai_api_key") or "")
            self.card_gemini.key_edit.setText(gemini_val)

            # Model selections (normalizing legacy/retired models like gemini-2.0-flash)
            from core.ai_client import normalize_model
            gemini_model = normalize_model(str(script.get("model") or api.get("gemini_model") or ""))
            if gemini_model:
                self.card_gemini.set_model(gemini_model)

            tts_model = str(api.get("gemini_tts_model") or "")
            if tts_model:
                self.card_gemini_tts.set_model(tts_model)

            img_model = str(video.get("ai_image_model") or api.get("gemini_image_model") or "")
            if img_model:
                self.card_gemini_image.set_model(img_model)

            if hasattr(self, "card_veo"):
                veo_model = str(video.get("ai_video_model") or api.get("veo_model") or "")
                if veo_model:
                    self.card_veo.set_model(veo_model)

            self._sync_shared_keys()
        finally:
            self._loading = False

    def apply_to(self, settings: Dict[str, Any]) -> None:
        paths = settings.setdefault("paths", {})
        app = settings.setdefault("app", {})
        script = settings.setdefault("script", {})
        video = settings.setdefault("video", {})
        api = settings.setdefault("api", {})

        # Paths
        h2 = self.card_h2mtts.path_edit.text().strip() or self.general_page.h2mtts_edit.text().strip()
        paths["h2mtts"] = h2
        paths["ffmpeg"] = self.general_page.ffmpeg_edit.text().strip() or "ffmpeg"
        paths["ffprobe"] = self.general_page.ffprobe_edit.text().strip() or "ffprobe"

        # App options
        app["retry"] = int(self.general_page.retry_spin.value())
        app["stop_on_error"] = self.general_page.stop_error_check.isChecked()
        app["log_dir"] = self.general_page.log_dir_edit.text().strip() or "logs"

        # API keys & models
        gemini_k = self.card_gemini.get_key()
        api["gemini_key"] = gemini_k
        if gemini_k:
            script["api_key"] = gemini_k
            video["ai_api_key"] = gemini_k

        api["gemini_model"] = self.card_gemini.get_model()
        script["model"] = self.card_gemini.get_model()

        api["gemini_tts_model"] = self.card_gemini_tts.get_model()
        api["gemini_image_model"] = self.card_gemini_image.get_model()
        video["ai_image_model"] = self.card_gemini_image.get_model()

        if hasattr(self, "card_veo"):
            api["veo_model"] = self.card_veo.get_model()
            video["ai_video_model"] = self.card_veo.get_model()

        self._settings = settings

    def apply_theme(self) -> None:
        self.card_gemini.apply_theme()
        self.card_gemini_tts.apply_theme()
        self.card_edge_tts.apply_theme()
        self.card_h2mtts.apply_theme()
        self.card_gemini_image.apply_theme()
        if hasattr(self, "card_veo"):
            self.card_veo.apply_theme()

