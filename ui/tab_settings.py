"""Settings tab: General options, system paths, and API & AI services."""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.request
import urllib.error
import urllib.parse
import zipfile

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
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

from core.settings import (
    ROOT,
    default_h2mtts_path,
    default_kokoro_path,
    default_piper_path,
    default_supertonic_path,
    normalize_h2mtts_path,
    normalize_kokoro_path,
    normalize_piper_path,
    normalize_supertonic_path,
)
from core.piper_tts_engine import sanitize_piper_config
from ui import theme
from ui.components.icons import icon, set_icon
from ui.components.page import PageHeader, icon_button, paint_status, wrap_scroll
from utils.logger import get_logger

logger = get_logger("ui.settings")

from core.ai_client import GEMINI_TEXT_MODELS
from core.piper_tts_engine import ALL_EDGE_VOICES, ALL_PIPER_MODELS, detect_gpu_status

DEFAULT_CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
DEFAULT_CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
DEFAULT_CF_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
DEFAULT_CF_TEXT_MODEL = "@cf/meta/llama-3.1-8b-instruct"

CLOUDFLARE_IMAGE_MODELS: List[Tuple[str, str]] = [
    ("FLUX.1 [schnell] (Khuyên dùng)", "@cf/black-forest-labs/flux-1-schnell"),
    ("FLUX.2 [klein] 9B", "@cf/black-forest-labs/flux-2-klein-9b"),
    ("FLUX.2 [klein] 4B", "@cf/black-forest-labs/flux-2-klein-4b"),
    ("FLUX.2 [dev]", "@cf/black-forest-labs/flux-2-dev"),
    ("Leonardo Lucid Origin", "@cf/leonardo/lucid-origin"),
    ("Leonardo Phoenix 1.0", "@cf/leonardo/phoenix-1.0"),
    ("SDXL Lightning (ByteDance)", "@cf/bytedance/stable-diffusion-xl-lightning"),
    ("DreamShaper 8 LCM", "@cf/lykon/dreamshaper-8-lcm"),
    ("Stable Diffusion v1.5 Inpainting", "@cf/runwayml/stable-diffusion-v1-5-inpainting"),
    ("Stable Diffusion XL Base 1.0", "@cf/stabilityai/stable-diffusion-xl-base-1.0"),
]

CLOUDFLARE_TEXT_MODELS: List[Tuple[str, str]] = [
    ("Llama 3.1 8B Instruct (Khuyên dùng)", "@cf/meta/llama-3.1-8b-instruct"),
    ("Llama 3.3 70B Instruct Fast", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
    ("DeepSeek R1 Distill Qwen 32B", "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"),
    ("Qwen 2.5 7B Instruct", "@cf/qwen/qwen2.5-7b-instruct"),
    ("Mistral 7B Instruct v0.2", "@cf/mistral/mistral-7b-instruct-v0.2"),
    ("Gemma 7B IT", "@cf/google/gemma-7b-it"),
]

EDGE_MULTILANG_VOICES: List[Tuple[str, str]] = list(ALL_EDGE_VOICES)


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
    target_drive = str(ROOT.anchor) if ROOT.anchor else ("C:/" if os.path.exists("C:/") else "/")
    norm_h2 = normalize_h2mtts_path(h2mtts_path)
    if norm_h2 and Path(norm_h2).drive:
        target_drive = Path(norm_h2).drive + "/"
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
    check_dir = Path(normalize_h2mtts_path(h2mtts_path))
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
            bg_color = QColor(theme.SUCCESS)
        else:
            bg_color = (
                QColor(theme.BORDER_STRONG)
                if theme.current_mode() == "dark"
                else QColor(theme.BORDER_CARD)
            )

        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 11, 11)

        thumb_color = QColor(theme.PRIMARY_TEXT)
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
                f"background: {theme.SURFACE_RAISED}; color: {theme.TEXT_MUTED}; "
                "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
            )
            top_row.addWidget(tag_lbl)

        top_row.addStretch(1)

        self.status_badge = QLabel("• Chưa kích hoạt / Thiếu key")
        self.status_badge.setStyleSheet(
            f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
            "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
        )
        top_row.addWidget(self.status_badge)

        self.switch = ToggleSwitch(checked=False)
        self.switch.toggled.connect(self._on_switch_toggled)
        top_row.addWidget(self.switch)
        layout.addLayout(top_row)

        # ── 2. Subtitle / Shared key info ────────────────────────────────────
        self.sub_lbl = QLabel()
        self.sub_lbl.setWordWrap(True)
        self.sub_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        if shared_from:
            self.sub_lbl.setText(
                f"Dùng chung API key {shared_from}\n"
                f"↔ Dùng chung API key {shared_from} – chưa có key. Dùng key riêng"
            )
        elif subtitle:
            self.sub_lbl.setText(subtitle)
        layout.addWidget(self.sub_lbl)

        # ── 3. Key input row & Model combo ───────────────────────────────────
        if has_key_input:
            key_row = QHBoxLayout()
            key_row.setSpacing(8)
            key_lbl = QLabel("API Key:")
            key_lbl.setFixedWidth(80)
            key_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
            self.key_edit = QLineEdit()
            self.key_edit.setPlaceholderText(key_placeholder or "Nhập API key...")
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.key_edit.textChanged.connect(self._on_key_changed)

            self.eye_btn = icon_button(
                "eye", "Hiện/Ẩn API key", role="muted", size=14, side=30
            )
            self.eye_btn.clicked.connect(self._toggle_eye)

            key_row.addWidget(key_lbl)
            key_row.addWidget(self.key_edit, 1)
            key_row.addWidget(self.eye_btn)
            layout.addLayout(key_row)

        if models:
            mod_row = QHBoxLayout()
            mod_row.setSpacing(8)
            mod_lbl = QLabel("Model AI:" if has_key_input else "Giọng đọc:")
            mod_lbl.setFixedWidth(80)
            mod_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
            self.model_combo = QComboBox()
            self.model_combo.setEditable(True)
            self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            for label, val in models:
                self.model_combo.addItem(label, val)
            self.model_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
            self.model_combo.currentTextChanged.connect(lambda _: self.changed.emit())
            mod_row.addWidget(mod_lbl)
            mod_row.addWidget(self.model_combo, 1)
            layout.addLayout(mod_row)

        # ── 4. Collapsible Endpoint ──────────────────────────────────────────
        if has_endpoint:
            ep_toggle_row = QHBoxLayout()
            self.ep_toggle_btn = QPushButton("Endpoint tùy chỉnh")
            self.ep_toggle_btn.setFlat(True)
            self.ep_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.ep_toggle_btn.setStyleSheet(
                f"color: {theme.ACCENT}; text-align: left; font-size: 11px; padding: 0px;"
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
            price_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
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
        self.ep_toggle_btn.setText("Endpoint tùy chỉnh")

    def _on_switch_toggled(self, checked: bool) -> None:
        key = self.get_key()
        if checked:
            if key or self.service_id == "edge_tts":
                self.set_status("valid", "• Đã kích hoạt")
            elif self.shared_from:
                self.set_status("needed", f"• Cần key {self.shared_from}")
            else:
                self.set_status("missing", "• Chưa kích hoạt / Thiếu key")
        else:
            self.set_status("needed", "• Đang tắt")
        self.changed.emit()

    def _on_key_changed(self, text: str) -> None:
        key = text.strip()
        if key:
            if not self.switch.isChecked():
                self.switch.blockSignals(True)
                self.switch.setChecked(True)
                self.switch.blockSignals(False)
            self.set_status("valid", "• Đã nhập key")
        elif self.shared_from:
            self.set_status("needed", f"• Cần key {self.shared_from}")
        else:
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            self.set_status("missing", "• Chưa kích hoạt / Thiếu key")
        self.changed.emit()

    def _save_clicked(self) -> None:
        key = self.get_key()
        if key or self.service_id == "edge_tts":
            self.switch.blockSignals(True)
            self.switch.setChecked(True)
            self.switch.blockSignals(False)
            self.set_status("valid", "• Đã lưu key")
        else:
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            self.set_status("missing", "• Chưa kích hoạt / Thiếu key")
        self.save_requested.emit(self.service_id, key)
        self.changed.emit()

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
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        elif kind == "needed":
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.WARNING}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        elif kind == "running":
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.BLUE}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        else:
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
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
        self.setStyleSheet(
            f"QFrame#ServiceCard {{ background-color: {theme.GROUP_BG}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 8px; }}"
        )


class _CheckCloudflareWorker(QThread):
    done = Signal(str, bool, str)
    models_discovered = Signal(str, list)

    def __init__(
        self,
        service_id: str,
        account_id: str,
        api_token: str,
        task: str = "Text-to-Image",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service_id = service_id
        self.account_id = account_id.strip()
        self.api_token = api_token.strip()
        self.task = task.strip()

    def run(self) -> None:
        if not self.account_id:
            self.done.emit(self.service_id, False, "Chưa nhập Cloudflare Account ID.")
            return
        if not self.api_token:
            self.done.emit(self.service_id, False, "Chưa nhập Cloudflare API Token.")
            return

        try:
            task_param = urllib.parse.quote(self.task)
            # Query Cloudflare Workers AI models search endpoint with task and source=1 (Cloudflare-hosted)
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/models/search?task={task_param}&source=1"
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "User-Agent": "AutoYout/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)
                    results = data.get("result", [])
                    discovered: List[Tuple[str, str]] = []
                    for m in results:
                        name = m.get("name", "").strip()
                        # Strictly verify Cloudflare-hosted model
                        if not name or not name.startswith("@cf/"):
                            continue
                        if m.get("source") is not None and m.get("source") != 1:
                            continue
                        short_name = name.split("/")[-1]
                        display_name = f"{short_name} ({name})"
                        discovered.append((display_name, name))

                    msg = (
                        f"Kết nối Cloudflare thành công! ({len(discovered)} models {self.task})"
                        if discovered
                        else "Kết nối Cloudflare thành công!"
                    )
                    if discovered:
                        self.models_discovered.emit(self.service_id, discovered)
                    self.done.emit(self.service_id, True, msg)
                    return
        except urllib.error.HTTPError as exc:
            self.done.emit(self.service_id, False, f"Lỗi HTTP {exc.code} (Token/Account ID không hợp lệ)")
            return
        except Exception as exc:
            self.done.emit(self.service_id, False, f"Lỗi kết nối: {exc}")
            return

        self.done.emit(self.service_id, False, "Không nhận được phản hồi từ Cloudflare.")


class CloudflareCard(QFrame):
    """Configuration card for Cloudflare Workers AI (Text or Image)."""

    changed = Signal()
    save_requested = Signal(str, str, str)  # service_id, account_id, token

    def __init__(
        self,
        service_id: str,
        title: str,
        tag: str = "Cloudflare-hosted",
        subtitle: str = "",
        task: str = "Text-to-Image",
        models: Optional[List[Tuple[str, str]]] = None,
        default_model: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service_id = service_id
        self.title_text = title
        self.tag_text = tag
        self.task = task
        self._checker: Optional[_CheckCloudflareWorker] = None

        self.setObjectName("CloudflareCard")
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
                f"background: {theme.SURFACE_RAISED}; color: {theme.TEXT_MUTED}; "
                "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
            )
            top_row.addWidget(tag_lbl)

        top_row.addStretch(1)

        self.status_badge = QLabel("• Đã kích hoạt")
        self.status_badge.setStyleSheet(
            f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
            "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
        )
        top_row.addWidget(self.status_badge)

        self.switch = ToggleSwitch(checked=True)
        self.switch.toggled.connect(self._on_switch_toggled)
        top_row.addWidget(self.switch)
        layout.addLayout(top_row)

        # ── 2. Subtitle ──────────────────────────────────────────────────────
        if subtitle:
            self.sub_lbl = QLabel(subtitle)
            self.sub_lbl.setWordWrap(True)
            self.sub_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(self.sub_lbl)

        # ── 3. Inputs Form ───────────────────────────────────────────────────
        # Row A: Account ID
        acc_row = QHBoxLayout()
        acc_row.setSpacing(8)
        acc_lbl = QLabel("Account ID:")
        acc_lbl.setFixedWidth(80)
        acc_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("Nhập Cloudflare Account ID...")
        self.account_edit.textChanged.connect(self._on_input_changed)
        acc_row.addWidget(acc_lbl)
        acc_row.addWidget(self.account_edit, 1)
        layout.addLayout(acc_row)

        # Row B: API Token + Eye button
        tok_row = QHBoxLayout()
        tok_row.setSpacing(8)
        tok_lbl = QLabel("API Token:")
        tok_lbl.setFixedWidth(80)
        tok_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Nhập Cloudflare API Token (Bearer)...")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.textChanged.connect(self._on_input_changed)

        self.eye_btn = icon_button("eye", "Hiện/Ẩn API Token", role="muted", size=14, side=30)
        self.eye_btn.clicked.connect(self._toggle_eye)

        tok_row.addWidget(tok_lbl)
        tok_row.addWidget(self.token_edit, 1)
        tok_row.addWidget(self.eye_btn)
        layout.addLayout(tok_row)

        # Row C: Model combo
        mod_row = QHBoxLayout()
        mod_row.setSpacing(8)
        mod_lbl = QLabel("Model AI:")
        mod_lbl.setFixedWidth(80)
        mod_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if models:
            for label, val in models:
                self.model_combo.addItem(label, val)
        if default_model:
            self.set_model(default_model)
        self.model_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.model_combo.currentTextChanged.connect(lambda _: self.changed.emit())
        mod_row.addWidget(mod_lbl)
        mod_row.addWidget(self.model_combo, 1)
        layout.addLayout(mod_row)

        # ── 4. Action buttons row ────────────────────────────────────────────
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        self.btn_save = QPushButton("Lưu key")
        set_icon(self.btn_save, "key", "muted", 12)
        self.btn_save.clicked.connect(self._save_clicked)
        act_row.addWidget(self.btn_save)

        self.btn_check = QPushButton("Check Connection")
        set_icon(self.btn_check, "check-circle", "accent", 12)
        self.btn_check.clicked.connect(self.check_connection)
        act_row.addWidget(self.btn_check)

        act_row.addStretch(1)
        layout.addLayout(act_row)

        self.apply_theme()

    def get_account_id(self) -> str:
        return self.account_edit.text().strip()

    def set_account_id(self, val: str) -> None:
        self.account_edit.setText(val or "")

    def get_token(self) -> str:
        return self.token_edit.text().strip()

    def set_token(self, val: str) -> None:
        self.token_edit.setText(val or "")

    def get_key(self) -> str:
        return self.get_token()

    def get_model(self) -> str:
        cur_idx = self.model_combo.currentIndex()
        cur_text = self.model_combo.currentText().strip()
        if cur_idx >= 0 and self.model_combo.itemText(cur_idx) == cur_text:
            data = self.model_combo.itemData(cur_idx)
            if data:
                return str(data)
        return cur_text

    def set_model(self, model_name: str) -> None:
        if not model_name:
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
        if self.token_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            set_icon(self.eye_btn, "eye", "accent", 14)
        else:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            set_icon(self.eye_btn, "eye", "muted", 14)

    def _on_switch_toggled(self, checked: bool) -> None:
        if checked:
            if self.get_token() and self.get_account_id():
                self.set_status("valid", "• Đã kích hoạt")
            else:
                self.set_status("missing", "• Chưa nhập Account ID / Token")
        else:
            self.set_status("needed", "• Đang tắt")
        self.changed.emit()

    def _on_input_changed(self) -> None:
        acc = self.get_account_id()
        tok = self.get_token()
        if acc and tok:
            if not self.switch.isChecked():
                self.switch.blockSignals(True)
                self.switch.setChecked(True)
                self.switch.blockSignals(False)
            self.set_status("valid", "• Sẵn sàng")
        else:
            self.set_status("missing", "• Thiếu Account ID hoặc Token")
        self.changed.emit()

    def _save_clicked(self) -> None:
        acc = self.get_account_id()
        tok = self.get_token()
        if acc and tok:
            self.set_status("valid", "• Đã lưu")
        else:
            self.set_status("missing", "• Thiếu Account ID hoặc Token")
        self.save_requested.emit(self.service_id, acc, tok)
        self.changed.emit()

    def check_connection(self) -> None:
        if self._checker is not None:
            return
        acc = self.get_account_id()
        tok = self.get_token()
        if not acc or not tok:
            self.set_status("missing", "• Cần Account ID & Token")
            self.btn_check.setToolTip("Vui lòng nhập cả Account ID và API Token trước khi kiểm tra.")
            return

        self.set_status("running", "• Đang kiểm tra & tải model…")
        self._checker = _CheckCloudflareWorker(self.service_id, acc, tok, self.task, self)
        self._checker.done.connect(self._on_check_done)
        self._checker.models_discovered.connect(self._on_models_discovered)
        self._checker.start()

    def _on_models_discovered(self, _sid: str, models: List[Tuple[str, str]]) -> None:
        existing_vals = {
            self.model_combo.itemData(i) for i in range(self.model_combo.count())
        }
        for label, val in models:
            if val and val not in existing_vals:
                self.model_combo.addItem(label, val)
                existing_vals.add(val)

    def _on_check_done(self, _sid: str, success: bool, msg: str) -> None:
        self._checker = None
        if success:
            self.set_status("valid", "• Hợp lệ")
        else:
            self.set_status("missing", f"• {msg}")
        self.btn_check.setToolTip(msg)

    def set_status(self, kind: str, text: str) -> None:
        self.status_badge.setText(text)
        if kind == "valid":
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        elif kind == "needed":
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.WARNING}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        elif kind == "running":
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.BLUE}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
        else:
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"QFrame#CloudflareCard {{ background-color: {theme.GROUP_BG}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 8px; }}"
        )


# ── H2MTTS Model Packages ────────────────────────────────────────────────────
H2M_PACKAGES: List[Dict[str, Any]] = [
    {
        "id": "turbo",
        "name": "H2M-TTS v3 Turbo",
        "tag": "Khuyên dùng · GPU & CPU",
        "tag_color": theme.ACCENT,
        "tag_bg": theme.SURFACE_RAISED,
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
        "tag_color": theme.WARNING,
        "tag_bg": theme.SURFACE_RAISED,
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
        "tag_color": theme.SUCCESS,
        "tag_bg": theme.SURFACE_RAISED,
        "repo": "hoangmui410/H2M-Voice-Packs",
        "size": "~5 MB",
        "desc": "Danh mục 25+ giọng đọc Bắc / Trung / Nam, preset cảm xúc và file embeddings.",
        "target_subpath": "voice-packs",
        "check_files": ["voice-catalog-v4.json", "voice-pack.json"],
        "core": True,
    },
]


class H2MDownloadCancelled(Exception):
    """Raised when the user cancels the download process."""


try:
    from tqdm.std import tqdm

    class H2MLogTqdm(tqdm):
        """Custom tqdm that emits download progress lines to logger for live console view."""
        cancel_check: Optional[Callable[[], bool]] = None

        def __init__(self, *args, **kwargs):
            if H2MLogTqdm.cancel_check and H2MLogTqdm.cancel_check():
                raise H2MDownloadCancelled("Tiến trình tải đã bị hủy.")
            if "file" not in kwargs or kwargs["file"] is None:
                kwargs["file"] = sys.stderr if getattr(sys, "stderr", None) is not None else io.StringIO()
            super().__init__(*args, **kwargs)
            self._last_log_pct = -1

        def update(self, n=1):
            if H2MLogTqdm.cancel_check and H2MLogTqdm.cancel_check():
                raise H2MDownloadCancelled("Tiến trình tải đã bị hủy.")
            super().update(n)
            if self.total and self.total > 0:
                pct = int((self.n / self.total) * 100)
                if (pct - self._last_log_pct >= 20 or pct == 100) and pct > 0:
                    self._last_log_pct = pct
                    desc = self.desc or "Dữ liệu"
                    logger.info(
                        "  → [Tiến trình %s]: %d%% (%s/%s)",
                        desc,
                        pct,
                        self.format_sizeof(self.n, "B"),
                        self.format_sizeof(self.total, "B"),
                    )
except Exception:
    H2MLogTqdm = None


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
        raw = str(target_root or "").strip()
        if not raw:
            self.target_root = Path(default_h2mtts_path())
        else:
            p = Path(raw)
            self.target_root = (ROOT / p).resolve() if not p.is_absolute() else p
        self.packages = packages
        self._is_cancelled = False
        self.errors: Dict[str, str] = {}

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        import io
        import os
        import sys

        # Ensure streams exist in windowed GUI (PyInstaller windowed apps have sys.stdout/stderr as None)
        if sys.stdout is None:
            sys.stdout = io.StringIO()
        if sys.stderr is None:
            sys.stderr = io.StringIO()

        # Disable console terminal progress bars from huggingface_hub so tqdm never writes to null streams
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

        try:
            from huggingface_hub.utils import disable_progress_bars
            disable_progress_bars()
        except Exception:
            pass

        from huggingface_hub import snapshot_download

        total_pkgs = len(self.packages)
        success_count = 0
        self.errors.clear()

        if H2MLogTqdm is not None:
            H2MLogTqdm.cancel_check = lambda: self._is_cancelled

        logger.info("=" * 60)
        logger.info("BẮT ĐẦU TẢI TÀI NGUYÊN H2MTTS: %d gói cần tải", total_pkgs)
        logger.info("Thư mục lưu trữ đích: %s", self.target_root)
        logger.info("=" * 60)

        try:
            for idx, pkg in enumerate(self.packages):
                if self._is_cancelled:
                    logger.info("Tiến trình tải đã bị hủy bởi người dùng.")
                    self.all_finished.emit(False, "Đã hủy tiến trình tải.")
                    return

                pkg_id = pkg["id"]
                repo_id = pkg["repo"]
                target_dir = self.target_root / pkg["target_subpath"]
                target_dir.mkdir(parents=True, exist_ok=True)

                current_pct = int((idx / total_pkgs) * 100)
                self.progress.emit(
                    current_pct,
                    f"Đang tải {pkg['name']} ({idx + 1}/{total_pkgs})…",
                )
                self.pkg_status_changed.emit(pkg_id, "downloading", "Đang tải…")

                logger.info(
                    "[%d/%d] Đang tải gói: %s (%s)",
                    idx + 1,
                    total_pkgs,
                    pkg["name"],
                    pkg.get("size", ""),
                )
                logger.info("  • Kho lưu trữ: https://huggingface.co/%s", repo_id)
                logger.info("  • Thư mục đích: %s", target_dir)

                try:
                    if self._is_cancelled:
                        raise H2MDownloadCancelled("Đã hủy tiến trình tải.")

                    # 1. Try primary Hugging Face endpoint
                    try:
                        snapshot_download(
                            repo_id=repo_id,
                            local_dir=str(target_dir),
                            repo_type="model",
                            tqdm_class=H2MLogTqdm,
                        )
                    except H2MDownloadCancelled:
                        raise
                    except Exception as primary_exc:
                        if self._is_cancelled:
                            raise H2MDownloadCancelled("Đã hủy tiến trình tải.")
                        logger.warning(
                            "Không tải được từ endpoint chính (%s). Tự động thử lại qua mirror https://hf-mirror.com...",
                            primary_exc,
                        )
                        snapshot_download(
                            repo_id=repo_id,
                            local_dir=str(target_dir),
                            repo_type="model",
                            endpoint="https://hf-mirror.com",
                            tqdm_class=H2MLogTqdm,
                        )

                    if self._is_cancelled:
                        raise H2MDownloadCancelled("Đã hủy tiến trình tải.")

                    if pkg.get("is_zip"):
                        zip_file = target_dir / "runtime.zip"
                        if zip_file.exists():
                            logger.info("  → Đang giải nén %s vào %s...", zip_file.name, target_dir)
                            self.progress.emit(
                                current_pct,
                                f"Đang giải nén runtime vào {target_dir.name}…",
                            )
                            try:
                                with zipfile.ZipFile(zip_file, "r") as zf:
                                    zf.extractall(target_dir)
                                logger.info("  ✓ Giải nén thành công: %s", zip_file.name)
                            except Exception as exc:
                                logger.warning("Lỗi giải nén runtime.zip: %s", exc)

                    success_count += 1
                    logger.info("✓ Tải thành công gói: %s", pkg["name"])
                    self.pkg_status_changed.emit(pkg_id, "installed", "✓ Đã có")
                except H2MDownloadCancelled:
                    logger.info("Đã dừng tiến trình tải gói %s theo yêu cầu người dùng.", pkg["name"])
                    logger.info("=" * 60)
                    logger.info("KẾT THÚC TIẾN TRÌNH: Đã hủy tải.")
                    logger.info("=" * 60)
                    self.pkg_status_changed.emit(pkg_id, "ready", "Chưa tải")
                    self.all_finished.emit(False, "Đã hủy tiến trình tải.")
                    return
                except Exception as exc:
                    err_text = str(exc)
                    logger.error("Lỗi khi tải %s: %s", repo_id, exc)
                    self.errors[pkg_id] = err_text
                    self.pkg_status_changed.emit(pkg_id, "error", "Lỗi tải")

            self.progress.emit(100, "Hoàn tất quá trình tải.")
            all_ok = success_count == total_pkgs
            if all_ok:
                msg = f"Đã tải thành công {success_count}/{total_pkgs} gói tài nguyên."
            else:
                first_err = next(iter(self.errors.values()), "Có lỗi xảy ra")
                # Keep message concise and readable
                short_err = first_err.split("\n")[0]
                if len(short_err) > 60:
                    short_err = short_err[:57] + "..."
                msg = f"Tải xong {success_count}/{total_pkgs} gói ({short_err})."

            logger.info("=" * 60)
            logger.info("KẾT THÚC TIẾN TRÌNH: %s", msg)
            logger.info("=" * 60)
            self.all_finished.emit(all_ok, msg)
        finally:
            if H2MLogTqdm is not None:
                H2MLogTqdm.cancel_check = None


class H2MTTSCard(QFrame):
    """Local H2MTTS engine, hardware specifications detector, and resource downloader."""

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
            f"background: {theme.SURFACE_RAISED}; color: {theme.TEXT_MUTED}; "
            "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
        )
        top_row.addWidget(tag_lbl)
        top_row.addStretch(1)

        self.status_badge = QLabel("Chưa cài")
        top_row.addWidget(self.status_badge)

        self._expanded = True
        self.btn_collapse = icon_button(
            "chevron-up", "Thu gọn / Mở rộng", role="muted", size=14, side=26
        )
        self.btn_collapse.clicked.connect(self.toggle_expanded)
        top_row.addWidget(self.btn_collapse)
        layout.addLayout(top_row)

        desc = QLabel(
            "Chạy model giọng tiếng Việt ngay trên máy: miễn phí, không giới hạn ký tự, "
            "25 giọng có sẵn (Bắc / Trung / Nam) và clone giọng từ 3–15 giây giọng mẫu. "
            "Mã nguồn mở Apache 2.0 – audio dùng thương mại được."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(desc)

        self.body_widget = QWidget(self)
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        layout.addWidget(self.body_widget)

        # ── 2. Hardware Specifications Box ───────────────────────────────────
        self.specs_box = QFrame()
        self.specs_box.setObjectName("SpecsBox")
        specs_layout = QVBoxLayout(self.specs_box)
        specs_layout.setContentsMargins(12, 10, 12, 10)
        specs_layout.setSpacing(8)

        rate_row = QHBoxLayout()
        self.rating_badge = QLabel("• Đánh giá cấu hình: TỐT - bản GPU")
        self.rating_badge.setStyleSheet(
            f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
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
        btn_redetect_text.setStyleSheet(f"color: {theme.ACCENT}; font-size: 11px;")
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
        self.lbl_note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        specs_layout.addWidget(self.lbl_note)
        body_layout.addWidget(self.specs_box)

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
        body_layout.addLayout(path_row)

        # ── 4. Download Manager Box ──────────────────────────────────────────
        self.downloads_box = QFrame()
        self.downloads_box.setObjectName("DownloadsBox")
        dl_layout = QVBoxLayout(self.downloads_box)
        dl_layout.setContentsMargins(12, 12, 12, 12)
        dl_layout.setSpacing(10)

        # Header of downloads box
        dl_header = QHBoxLayout()
        dl_title = QLabel("📦 Tải tài nguyên mô hình H2MTTS")
        dl_title_font = QFont()
        dl_title_font.setBold(True)
        dl_title_font.setPointSize(10)
        dl_title.setFont(dl_title_font)
        dl_header.addWidget(dl_title)
        dl_header.addStretch(1)
        dl_layout.addLayout(dl_header)

        dl_desc = QLabel(
            "Tự động tải các model và voice-packs H2MTTS về thư mục trên máy để chạy offline."
        )
        dl_desc.setWordWrap(True)
        dl_desc.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        dl_layout.addWidget(dl_desc)

        # Quick action buttons bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_install = QPushButton("🚀 Tải các phần bắt buộc (~385 MB)")
        self.btn_install.setObjectName("primaryButton")
        set_icon(self.btn_install, "download", "white", 13)
        self.btn_install.clicked.connect(self._download_required_clicked)
        btn_bar.addWidget(self.btn_install)

        self.btn_check_engine = QPushButton("🔄 Quét lại")
        set_icon(self.btn_check_engine, "check-circle", "muted", 12)
        self.btn_check_engine.clicked.connect(self.check_packages_status)
        btn_bar.addWidget(self.btn_check_engine)

        self.btn_clean_engine = QPushButton("Dọn dẹp")
        self.btn_clean_engine.setToolTip("Dọn dẹp file tạm (.part, .tmp) hoặc gỡ bỏ model để giải phóng dung lượng")
        set_icon(self.btn_clean_engine, "trash", "danger", 12)
        self.btn_clean_engine.clicked.connect(self._clean_models_clicked)
        btn_bar.addWidget(self.btn_clean_engine)

        btn_bar.addStretch(1)
        dl_layout.addLayout(btn_bar)

        # Progress panel (hidden by default)
        self.progress_container = QWidget()
        prog_layout = QVBoxLayout(self.progress_container)
        prog_layout.setContentsMargins(0, 4, 0, 4)
        prog_layout.setSpacing(4)

        prog_top = QHBoxLayout()
        self.lbl_progress = QLabel("Đang chuẩn bị tải…")
        self.lbl_progress.setStyleSheet(f"font-size: 11px; color: {theme.ACCENT}; font-weight: bold;")
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

            sub_lbl = QLabel(f"{pkg['size']} — {pkg['desc']}")
            sub_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10.5px;")
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

            btn_trash = icon_button("trash", f"Xoá {pkg['name']} khỏi máy", role="danger", size=13, side=28)
            btn_trash.setToolTip(f"Xoá gói '{pkg['name']}' khỏi máy để giải phóng dung lượng")
            btn_trash.clicked.connect(lambda _, p=pkg: self._delete_single_clicked(p))
            btn_trash.setVisible(False)
            row_layout.addWidget(btn_trash)

            self.pkg_widgets[pkg["id"]] = {
                "frame": row_frame,
                "status_badge": status_badge,
                "btn_dl": btn_dl,
                "btn_trash": btn_trash,
                "pkg": pkg,
            }
            dl_layout.addWidget(row_frame)

        body_layout.addWidget(self.downloads_box)

        # Status text line
        self.engine_status = QLabel("")
        body_layout.addWidget(self.engine_status)

        self.apply_theme()
        self.detect_specs()
        self.check_packages_status()

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.body_widget.setVisible(self._expanded)
        set_icon(
            self.btn_collapse,
            "chevron-up" if self._expanded else "chevron-down",
            "muted",
            14,
        )
        self.btn_collapse.setToolTip("Thu gọn" if self._expanded else "Mở rộng")

    @staticmethod
    def _make_spec_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-weight: bold; font-size: 11px;")
        lbl.setFixedWidth(80)
        return lbl

    def _browse_dir(self) -> None:
        start = self.path_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục H2MTTS", start)
        if chosen:
            chosen = str(Path(chosen).resolve().as_posix())
            self.path_edit.setText(chosen)
            logger.info("Đã chọn thư mục H2MTTS: %s", chosen)
            self.detect_specs()
            self.check_packages_status()
            self.changed.emit()

    def _on_path_changed(self, _text: str) -> None:
        self.detect_specs()
        self.check_packages_status()
        self.changed.emit()

    def check_packages_status(self, errors: Optional[Dict[str, str]] = None) -> None:
        raw = self.path_edit.text().strip()
        if not raw:
            path = Path(default_h2mtts_path())
        else:
            p = Path(raw)
            path = (ROOT / p).resolve() if not p.is_absolute() else p
        all_core_ok = True
        has_any = False
        errors = errors or {}

        for pkg in H2M_PACKAGES:
            pkg_id = pkg["id"]
            w = self.pkg_widgets.get(pkg_id)
            if not w:
                continue
            is_installed = self._is_pkg_installed(path, pkg)
            if is_installed:
                has_any = True
                w["status_badge"].setText("✓ Đã có")
                w["status_badge"].setToolTip("Gói tài nguyên đã được tải đầy đủ.")
                w["status_badge"].setStyleSheet(
                    f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                    "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                )
                w["btn_dl"].setText("Tải lại")
                w["btn_trash"].setVisible(True)
                w["btn_trash"].setEnabled(True)
            elif pkg_id in errors:
                if pkg.get("core"):
                    all_core_ok = False
                w["status_badge"].setText("Lỗi tải")
                w["status_badge"].setToolTip(f"Lỗi: {errors[pkg_id]}")
                w["status_badge"].setStyleSheet(
                    f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                    "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                )
                w["btn_dl"].setText("Thử lại")
                w["btn_trash"].setVisible(False)
            else:
                if pkg.get("core"):
                    all_core_ok = False
                w["status_badge"].setText("Chưa tải")
                w["status_badge"].setToolTip("Gói chưa được tải về máy.")
                w["status_badge"].setStyleSheet(
                    f"background: {theme.SURFACE_RAISED}; color: {theme.WARNING}; "
                    "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                )
                w["btn_dl"].setText("Tải về")
                w["btn_trash"].setVisible(False)

        if path.exists() and all_core_ok:
            self.status_badge.setText("Đã cài đủ")
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Đã cài đủ phần bắt buộc")
        elif path.exists() and has_any:
            self.status_badge.setText("Thiếu gói")
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.WARNING}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Tải các phần bắt buộc (~385 MB)")
        else:
            self.status_badge.setText("Chưa cài")
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Tải các phần bắt buộc (~385 MB)")

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

    def _delete_single_clicked(self, pkg: Dict[str, Any]) -> None:
        raw = self.path_edit.text().strip()
        path = Path(raw) if raw else Path(default_h2mtts_path())
        reply = QMessageBox.question(
            self,
            "Xác nhận xoá model H2MTTS",
            f"Bạn có chắc muốn xoá gói '{pkg['name']}' khỏi máy tính?\nThao tác này sẽ giải phóng dung lượng trên ổ đĩa.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._delete_pkg_files(path, pkg)
        self.check_packages_status()
        self.detect_specs()
        self.changed.emit()

    def _delete_pkg_files(self, root: Path, pkg: Dict[str, Any]) -> None:
        if not root.exists():
            return
        target = root / pkg.get("target_subpath", "")
        deleted = False
        for f in pkg.get("check_files", []):
            fp = target / f
            if fp.is_file():
                try:
                    fp.unlink()
                    deleted = True
                except Exception as exc:
                    logger.warning("Không thể xoá file %s: %s", fp, exc)
            elif fp.is_dir():
                try:
                    shutil.rmtree(fp, ignore_errors=True)
                    deleted = True
                except Exception as exc:
                    logger.warning("Không thể xoá thư mục %s: %s", fp, exc)
        if deleted:
            paint_status(self.engine_status, "success")
            self.engine_status.setText(f"Đã xoá gói '{pkg['name']}' khỏi máy.")

    def _clean_models_clicked(self) -> None:
        raw = self.path_edit.text().strip()
        path = Path(raw) if raw else Path(default_h2mtts_path())
        if not path.exists():
            QMessageBox.information(self, "Dọn dẹp", "Thư mục H2MTTS chưa tồn tại trên máy.")
            return
        cleaned_files = 0
        cleaned_bytes = 0
        for pattern in ("*.part", "*.tmp"):
            for f in path.rglob(pattern):
                if f.is_file():
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        cleaned_files += 1
                        cleaned_bytes += sz
                    except Exception:
                        pass
        mb = round(cleaned_bytes / (1024 * 1024), 2)
        msg = f"Đã dọn dẹp {cleaned_files} file tạm ({mb} MB)." if cleaned_files > 0 else "Không có file tạm nào cần dọn."
        reply = QMessageBox.question(
            self,
            "Dọn dẹp H2MTTS",
            f"{msg}\n\nBạn có muốn xoá TOÀN BỘ model H2MTTS đã tải để giải phóng dung lượng hoàn toàn không?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for pkg in H2M_PACKAGES:
                self._delete_pkg_files(path, pkg)
            self.check_packages_status()
            self.detect_specs()
            self.changed.emit()
            QMessageBox.information(self, "Hoàn tất", "Đã dọn dẹp sạch toàn bộ model H2MTTS.")
        elif cleaned_files > 0:
            self.check_packages_status()
            self.changed.emit()
            QMessageBox.information(self, "Hoàn tất", f"Đã xoá {cleaned_files} file tạm.")

    def _download_required_clicked(self) -> None:
        required = [p for p in H2M_PACKAGES if p.get("core")]
        self._start_download(required)

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
        self.lbl_progress.setText("Đang khởi tạo kết nối tải dữ liệu…")
        self.btn_cancel_dl.setText("Hủy")
        self.btn_cancel_dl.setEnabled(True)
        self.btn_install.setEnabled(False)
        logger.info("Bắt đầu tác vụ tải %d gói tài nguyên H2MTTS vào thư mục: %s", len(pkgs), root_path)

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
                f"background: {theme.SURFACE_RAISED}; color: {theme.BLUE}; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
            w["status_badge"].setToolTip("Đang tải dữ liệu từ Hugging Face…")
        elif status_kind == "installed":
            w["status_badge"].setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
            w["status_badge"].setToolTip("Gói tài nguyên đã cài đặt đầy đủ.")
            w["btn_dl"].setText("Tải lại")
        elif status_kind == "error":
            w["status_badge"].setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
            w["btn_dl"].setText("Thử lại")

    def _on_download_finished(self, success: bool, message: str) -> None:
        errors = dict(getattr(self._downloader, "errors", {})) if self._downloader else {}
        self._downloader = None
        self.btn_install.setEnabled(True)
        self.btn_cancel_dl.setText("Đóng")
        self.btn_cancel_dl.setEnabled(True)
        self.lbl_progress.setText(message)
        if success:
            paint_status(self.engine_status, "success")
        else:
            paint_status(self.engine_status, "warning")
        self.engine_status.setText(message)
        self.check_packages_status(errors=errors)
        self.detect_specs()
        self.changed.emit()

    def _cancel_download_clicked(self) -> None:
        if self._downloader and self._downloader.isRunning():
            self._downloader.cancel()
            self.lbl_progress.setText("Đang dừng tiến trình tải…")
            self.btn_cancel_dl.setEnabled(False)
        else:
            self.progress_container.setVisible(False)

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
        self.setStyleSheet(
            f"QFrame#H2MTTSCard {{ background-color: {theme.GROUP_BG}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 8px; }}"
        )
        self.specs_box.setStyleSheet(
            f"QFrame#SpecsBox {{ background-color: {theme.SURFACE_ALT}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 6px; }}"
        )
        if hasattr(self, "downloads_box"):
            self.downloads_box.setStyleSheet(
                f"QFrame#DownloadsBox {{ background-color: {theme.SURFACE_ALT}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 6px; }}"
            )
        if hasattr(self, "pkg_widgets"):
            for w in self.pkg_widgets.values():
                w["frame"].setStyleSheet(
                    f"QFrame#PkgRow {{ background-color: {theme.SURFACE}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 6px; }}"
                )


# ── PiperTTS Model Packages (Runtime + Full Catalog of 180+ Voices) ──────────
def _build_piper_packages() -> List[Dict[str, Any]]:
    pkgs: List[Dict[str, Any]] = [
        {
            "id": "piper_runtime",
            "name": "Piper TTS Engine (Windows x64)",
            "tag": "Bắt buộc · Runtime",
            "tag_color": theme.ACCENT,
            "tag_bg": theme.SURFACE_RAISED,
            "url": "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip",
            "is_zip_url": True,
            "size": "~20 MB",
            "desc": "Bộ thực thi Piper TTS C++ ONNX siêu tốc (piper.exe + espeak-ng-data).",
            "target_subpath": ".",
            "check_files": ["piper.exe", "piper/piper.exe"],
            "core": True,
            "required": True,
        }
    ]
    for m in ALL_PIPER_MODELS:
        key = str(m.get("key") or "").strip()
        onnx_rel = str(m.get("onnx_rel") or "").strip()
        json_rel = str(m.get("json_rel") or "").strip()
        if not key or not onnx_rel:
            continue
        lang_code = str(m.get("lang") or "").lower()
        locale = str(m.get("locale") or lang_code)
        native = str(m.get("name_native") or lang_code)
        en_name = str(m.get("name_english") or "")
        country = str(m.get("country") or "")
        quality = str(m.get("quality") or "medium")
        speakers = int(m.get("num_speakers") or 1)
        size_mb = m.get("size_mb", 60)

        if lang_code in ("vi", "en", "zh", "fr", "es", "de", "ru"):
            tag_color = theme.BLUE
            tag_text = f"{native} ({locale})"
        else:
            tag_color = theme.TEXT_MUTED
            tag_text = f"{native} ({locale})"

        files = [(onnx_rel, f"models/{key}.onnx")]
        if json_rel:
            files.append((json_rel, f"models/{key}.onnx.json"))

        spk_str = f" · {speakers} giọng" if speakers > 1 else ""
        pkgs.append(
            {
                "id": f"piper_{key}",
                "name": f"[{locale}] {m.get('name', key)} ({quality.capitalize()}) — {native}",
                "tag": tag_text,
                "tag_color": tag_color,
                "tag_bg": theme.SURFACE_RAISED,
                "repo": "rhasspy/piper-voices",
                "files": files,
                "size": f"~{size_mb} MB",
                "desc": f"Model Piper ONNX: {key} ({en_name} - {country}{spk_str}).",
                "target_subpath": "models",
                "check_files": [f"{key}.onnx", f"voices/{lang_code}/{key}.onnx"],
                "core": False,
                "required": False,
            }
        )
    return pkgs


PIPER_PACKAGES: List[Dict[str, Any]] = _build_piper_packages()


class PiperDownloadWorker(QThread):
    progress = Signal(int, str)
    pkg_status_changed = Signal(str, str, str)
    all_finished = Signal(bool, str)

    def __init__(
        self,
        target_root: str,
        packages: List[Dict[str, Any]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        raw = str(target_root or "").strip()
        if not raw:
            self.target_root = Path(default_piper_path())
        else:
            p = Path(raw)
            self.target_root = (ROOT / p).resolve() if not p.is_absolute() else p
        self.packages = packages
        self._is_cancelled = False
        self.errors: Dict[str, str] = {}

    def cancel(self) -> None:
        self._is_cancelled = True

    def _download_url_to_file(self, url: str, dest: Path, progress_prefix: str = "") -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest_part = dest.with_name(f"{dest.name}.part")
        req = urllib.request.Request(url, headers={"User-Agent": "AutoYout/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest_part, "wb") as out_f:
                total_size = int(resp.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                last_emit = time.time()
                while True:
                    if self._is_cancelled:
                        raise H2MDownloadCancelled("Đã hủy tiến trình tải.")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_emit > 0.4:
                        last_emit = now
                        if total_size > 0:
                            dl_mb = downloaded / (1024 * 1024)
                            tot_mb = total_size / (1024 * 1024)
                            sub_pct = int((downloaded / total_size) * 100)
                            self.progress.emit(
                                sub_pct,
                                f"{progress_prefix} ({dl_mb:.1f} MB / {tot_mb:.1f} MB)" if progress_prefix else f"Đang tải {dl_mb:.1f}/{tot_mb:.1f} MB…"
                            )
            if dest_part.is_file():
                if dest.is_file():
                    dest.unlink()
                dest_part.rename(dest)
        except Exception:
            if dest_part.is_file():
                try:
                    dest_part.unlink()
                except OSError:
                    pass
            raise

    def run(self) -> None:
        total_pkgs = len(self.packages)
        success_count = 0
        self.errors.clear()
        self.target_root.mkdir(parents=True, exist_ok=True)

        for idx, pkg in enumerate(self.packages):
            if self._is_cancelled:
                self.all_finished.emit(False, "Đã hủy tiến trình tải.")
                return

            pkg_id = pkg["id"]
            current_pct = int((idx / max(total_pkgs, 1)) * 100)
            prefix = f"Đang tải {pkg['name']} ({idx + 1}/{total_pkgs})"
            self.progress.emit(current_pct, f"{prefix}…")
            self.pkg_status_changed.emit(pkg_id, "downloading", "Đang tải…")

            try:
                if pkg.get("is_zip_url"):
                    zip_path = self.target_root / "piper_runtime.zip"
                    self._download_url_to_file(pkg["url"], zip_path, progress_prefix=prefix)
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(self.target_root)
                    try:
                        zip_path.unlink()
                    except OSError:
                        pass
                elif pkg.get("files"):
                    repo = pkg["repo"]
                    for remote_rel, local_rel in pkg["files"]:
                        if self._is_cancelled:
                            raise H2MDownloadCancelled("Đã hủy tiến trình tải.")
                        dest_file = self.target_root / local_rel
                        url = f"https://huggingface.co/{repo}/resolve/main/{remote_rel}"
                        try:
                            self._download_url_to_file(url, dest_file, progress_prefix=prefix)
                        except H2MDownloadCancelled:
                            raise
                        except Exception:
                            mirror_url = f"https://hf-mirror.com/{repo}/resolve/main/{remote_rel}"
                            self._download_url_to_file(mirror_url, dest_file, progress_prefix=prefix)
                        if dest_file.name.endswith(".onnx.json") or dest_file.suffix == ".json":
                            sanitize_piper_config(dest_file)

                success_count += 1
                self.pkg_status_changed.emit(pkg_id, "installed", "✓ Đã có")
            except H2MDownloadCancelled:
                self.pkg_status_changed.emit(pkg_id, "ready", "Chưa tải")
                self.all_finished.emit(False, "Đã hủy tiến trình tải.")
                return
            except Exception as exc:
                logger.error("Lỗi khi tải gói %s (%s): %s", pkg_id, pkg.get("name"), exc, exc_info=True)
                self.errors[pkg_id] = str(exc)
                self.pkg_status_changed.emit(pkg_id, "error", "Lỗi tải")

        self.progress.emit(100, "Hoàn tất quá trình tải.")
        all_ok = success_count == total_pkgs
        msg = (
            f"Đã tải thành công {success_count}/{total_pkgs} gói tài nguyên."
            if all_ok
            else f"Tải xong {success_count}/{total_pkgs} gói tài nguyên."
        )
        self.all_finished.emit(all_ok, msg)


class PiperTTSCard(QFrame):
    """Local PiperTTS engine card with collapsible body and model downloader."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PiperTTSCard")
        self._downloader: Optional[PiperDownloadWorker] = None
        self.pkg_widgets: Dict[str, Dict[str, Any]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # ── 1. Header ────────────────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_lbl = QLabel("Giọng đọc local – PiperTTS")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(10)
        title_lbl.setFont(title_font)
        top_row.addWidget(title_lbl)

        tag_lbl = QLabel("Đa ngôn ngữ · Siêu nhẹ")
        tag_lbl.setStyleSheet(
            f"background: {theme.SURFACE_RAISED}; color: {theme.TEXT_MUTED}; "
            "border-radius: 4px; padding: 2px 6px; font-size: 11px;"
        )
        top_row.addWidget(tag_lbl)
        top_row.addStretch(1)

        self.status_badge = QLabel("Chưa cài")
        top_row.addWidget(self.status_badge)

        self._expanded = True
        self.btn_collapse = icon_button(
            "chevron-up", "Thu gọn / Mở rộng", role="muted", size=14, side=26
        )
        self.btn_collapse.clicked.connect(self.toggle_expanded)
        top_row.addWidget(self.btn_collapse)
        layout.addLayout(top_row)

        desc = QLabel(
            "Chạy model neural TTS đa ngôn ngữ (Việt / Anh / Trung / Nhật / Hàn / Pháp...) "
            "ngay trên CPU với tốc độ cực nhanh, dung lượng nhẹ (~60MB/model), hoạt động hoàn toàn offline."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(desc)

        self.body_widget = QWidget(self)
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        layout.addWidget(self.body_widget)

        # ── 2. Path row ──────────────────────────────────────────────────────
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_lbl = QLabel("Thư mục PiperTTS:")
        path_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.path_edit = QLineEdit(default_piper_path())
        self.path_edit.textChanged.connect(self._on_path_changed)
        btn_browse = icon_button(
            "folder-open", "Chọn thư mục PiperTTS…", role="muted", size=14, side=30
        )
        btn_browse.clicked.connect(self._browse_dir)

        path_row.addWidget(path_lbl)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(btn_browse)
        body_layout.addLayout(path_row)

        # ── 3. Download Manager Box ──────────────────────────────────────────
        self.downloads_box = QFrame()
        self.downloads_box.setObjectName("DownloadsBox")
        dl_layout = QVBoxLayout(self.downloads_box)
        dl_layout.setContentsMargins(12, 12, 12, 12)
        dl_layout.setSpacing(10)

        dl_header = QHBoxLayout()
        dl_title = QLabel("📦 Tải tài nguyên mô hình PiperTTS")
        dl_title_font = QFont()
        dl_title_font.setBold(True)
        dl_title_font.setPointSize(10)
        dl_title.setFont(dl_title_font)
        dl_header.addWidget(dl_title)
        dl_header.addStretch(1)
        dl_layout.addLayout(dl_header)

        dl_desc = QLabel(
            "Tự động tải bộ chạy PiperTTS và các model giọng đọc ONNX đa ngôn ngữ về thư mục trên máy."
        )
        dl_desc.setWordWrap(True)
        dl_desc.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        dl_layout.addWidget(dl_desc)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_install = QPushButton("🚀 Tải các phần bắt buộc (~20 MB)")
        self.btn_install.setObjectName("primaryButton")
        set_icon(self.btn_install, "download", "white", 13)
        self.btn_install.clicked.connect(self._download_required_clicked)
        btn_bar.addWidget(self.btn_install)

        self.btn_check_engine = QPushButton("🔄 Quét lại")
        set_icon(self.btn_check_engine, "check-circle", "muted", 12)
        self.btn_check_engine.clicked.connect(self.check_packages_status)
        btn_bar.addWidget(self.btn_check_engine)

        self.btn_clean_engine = QPushButton("Dọn dẹp")
        self.btn_clean_engine.setToolTip("Dọn dẹp file tạm (.part, .tmp) hoặc gỡ bỏ model để giải phóng dung lượng")
        set_icon(self.btn_clean_engine, "trash", "danger", 12)
        self.btn_clean_engine.clicked.connect(self._clean_models_clicked)
        btn_bar.addWidget(self.btn_clean_engine)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Tìm model / ngôn ngữ Piper (vd: vi, en, lessac, vais1000, zh, fr, de)...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(260)
        self.search_edit.textChanged.connect(self._filter_packages)
        btn_bar.addWidget(self.search_edit, 1)

        dl_layout.addLayout(btn_bar)

        self.progress_container = QWidget()
        prog_layout = QVBoxLayout(self.progress_container)
        prog_layout.setContentsMargins(0, 4, 0, 4)
        prog_layout.setSpacing(4)

        prog_top = QHBoxLayout()
        self.lbl_progress = QLabel("Đang chuẩn bị tải…")
        self.lbl_progress.setStyleSheet(f"font-size: 11px; color: {theme.ACCENT}; font-weight: bold;")
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

        self.pkg_scroll = QScrollArea()
        self.pkg_scroll.setWidgetResizable(True)
        self.pkg_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pkg_scroll.setMinimumHeight(320)
        self.pkg_scroll.setMaximumHeight(420)
        self.pkg_list_host = QWidget()
        pkg_list_layout = QVBoxLayout(self.pkg_list_host)
        pkg_list_layout.setContentsMargins(0, 0, 4, 0)
        pkg_list_layout.setSpacing(8)

        for pkg in PIPER_PACKAGES:
            row_frame = QFrame()
            row_frame.setObjectName("PkgRow")
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(10)

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

            sub_lbl = QLabel(f"{pkg['size']} — {pkg['desc']}")
            sub_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10.5px;")
            sub_lbl.setWordWrap(True)
            info_col.addWidget(sub_lbl)
            row_layout.addLayout(info_col, 1)

            status_badge = QLabel("Chưa kiểm tra")
            status_badge.setFixedWidth(80)
            status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addWidget(status_badge)

            btn_dl = QPushButton("Tải về")
            btn_dl.setFixedWidth(70)
            btn_dl.clicked.connect(lambda _, p=pkg: self._download_single_clicked(p))
            row_layout.addWidget(btn_dl)

            btn_trash = icon_button("trash", f"Xoá {pkg['name']} khỏi máy", role="danger", size=13, side=28)
            btn_trash.setToolTip(f"Xoá gói '{pkg['name']}' khỏi máy để giải phóng dung lượng")
            btn_trash.clicked.connect(lambda _, p=pkg: self._delete_single_clicked(p))
            btn_trash.setVisible(False)
            row_layout.addWidget(btn_trash)

            search_blob = f"{pkg['id']} {pkg['name']} {pkg['tag']} {pkg['desc']}".lower()
            self.pkg_widgets[pkg["id"]] = {
                "frame": row_frame,
                "row": row_frame,
                "status_badge": status_badge,
                "btn_dl": btn_dl,
                "btn_trash": btn_trash,
                "pkg": pkg,
                "search_blob": search_blob,
                "state": None,
            }
            pkg_list_layout.addWidget(row_frame)

        pkg_list_layout.addStretch(1)
        self.pkg_scroll.setWidget(self.pkg_list_host)
        dl_layout.addWidget(self.pkg_scroll)

        body_layout.addWidget(self.downloads_box)

        self.engine_status = QLabel("")
        body_layout.addWidget(self.engine_status)

        self.apply_theme()
        self.check_packages_status()

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.body_widget.setVisible(self._expanded)
        set_icon(
            self.btn_collapse,
            "chevron-up" if self._expanded else "chevron-down",
            "muted",
            14,
        )
        self.btn_collapse.setToolTip("Thu gọn" if self._expanded else "Mở rộng")

    def _browse_dir(self) -> None:
        start = self.path_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục PiperTTS", start)
        if chosen:
            chosen = str(Path(chosen).resolve().as_posix())
            self.path_edit.setText(chosen)
            self.check_packages_status()
            self.changed.emit()

    def _on_path_changed(self, _text: str) -> None:
        self.check_packages_status()
        self.changed.emit()

    def _filter_packages(self, text: str) -> None:
        query = str(text or "").strip().lower()
        terms = [t for t in query.split() if t]
        for pid, w in self.pkg_widgets.items():
            if not terms or pid == "piper_runtime":
                w["frame"].setVisible(True)
            else:
                blob = w.get("search_blob", "")
                w["frame"].setVisible(all(t in blob for t in terms))

    @staticmethod
    def _is_pkg_installed(root: Path, pkg: Dict[str, Any]) -> bool:
        if not root.exists():
            return False
        target = root / pkg["target_subpath"]
        if not target.exists():
            return False
        for f in pkg.get("check_files", []):
            c1 = target / f
            c2 = root / f
            if (c1.is_file() and c1.stat().st_size > 0) or (c2.is_file() and c2.stat().st_size > 0):
                return True
        return False

    def check_packages_status(self, errors: Optional[Dict[str, str]] = None) -> None:
        raw = self.path_edit.text().strip()
        if not raw:
            path = Path(default_piper_path())
        else:
            p = Path(raw)
            path = (ROOT / p).resolve() if not p.is_absolute() else p
        all_core_ok = True
        has_any = False
        errors = errors or {}
        root_exists = path.exists()

        for pkg in PIPER_PACKAGES:
            pkg_id = pkg["id"]
            w = self.pkg_widgets.get(pkg_id)
            if not w:
                continue
            is_installed = root_exists and self._is_pkg_installed(path, pkg)
            if is_installed:
                has_any = True
                if w.get("state") != "installed":
                    w["state"] = "installed"
                    w["status_badge"].setText("✓ Đã có")
                    w["status_badge"].setStyleSheet(
                        f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                        "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                    )
                    w["btn_dl"].setText("Tải lại")
                w["btn_trash"].setVisible(True)
                w["btn_trash"].setEnabled(True)
            elif pkg_id in errors:
                if pkg.get("core"):
                    all_core_ok = False
                if w.get("state") != "error":
                    w["state"] = "error"
                    w["status_badge"].setText("Lỗi tải")
                    w["status_badge"].setToolTip(f"Lỗi: {errors[pkg_id]}")
                    w["status_badge"].setStyleSheet(
                        f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                        "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                    )
                    w["btn_dl"].setText("Thử lại")
                w["btn_trash"].setVisible(False)
            else:
                if pkg.get("core"):
                    all_core_ok = False
                if w.get("state") != "missing":
                    w["state"] = "missing"
                    w["status_badge"].setText("Chưa tải")
                    w["status_badge"].setStyleSheet(
                        f"background: {theme.SURFACE_RAISED}; color: {theme.WARNING}; "
                        "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
                    )
                    w["btn_dl"].setText("Tải về")
                w["btn_trash"].setVisible(False)

        if path.exists() and all_core_ok:
            self.status_badge.setText("Đã cài đủ")
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Đã cài đủ phần bắt buộc")
        elif path.exists() and has_any:
            self.status_badge.setText("Thiếu gói")
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.WARNING}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Tải các phần bắt buộc (~20 MB)")
        else:
            self.status_badge.setText("Chưa cài")
            self.status_badge.setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("🚀 Tải các phần bắt buộc (~20 MB)")

    def _download_single_clicked(self, pkg: Dict[str, Any]) -> None:
        self._start_download([pkg])

    def _delete_single_clicked(self, pkg: Dict[str, Any]) -> None:
        raw = self.path_edit.text().strip()
        path = Path(raw) if raw else Path(default_piper_path())
        reply = QMessageBox.question(
            self,
            "Xác nhận xoá model PiperTTS",
            f"Bạn có chắc muốn xoá gói '{pkg['name']}' khỏi máy tính?\nThao tác này sẽ giải phóng dung lượng trên ổ đĩa.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._delete_pkg_files(path, pkg)
        self.check_packages_status()
        self.changed.emit()

    def _delete_pkg_files(self, root: Path, pkg: Dict[str, Any]) -> None:
        if not root.exists():
            return
        if pkg["id"] == "piper_runtime":
            for folder_name in ("piper", "runtime"):
                runtime_dir = root / folder_name
                if runtime_dir.is_dir():
                    try:
                        shutil.rmtree(runtime_dir, ignore_errors=True)
                    except Exception as exc:
                        logger.warning("Không thể xoá thư mục %s: %s", runtime_dir, exc)
            for f in ("piper.exe", "piper"):
                p_f = root / f
                if p_f.is_file():
                    try:
                        p_f.unlink()
                    except Exception:
                        pass
        else:
            target = root / pkg.get("target_subpath", "")
            for f in pkg.get("check_files", []):
                for base_dir in (target, root):
                    fp = base_dir / f
                    if fp.is_file():
                        try:
                            fp.unlink()
                        except Exception as exc:
                            logger.warning("Không thể xoá file %s: %s", fp, exc)
                    json_fp = fp.with_name(f"{fp.name}.json")
                    if json_fp.is_file():
                        try:
                            json_fp.unlink()
                        except Exception:
                            pass
                    json_fp2 = fp.with_suffix(".json")
                    if json_fp2.is_file():
                        try:
                            json_fp2.unlink()
                        except Exception:
                            pass
        paint_status(self.engine_status, "success")
        self.engine_status.setText(f"Đã xoá gói '{pkg['name']}' khỏi máy.")

    def _clean_models_clicked(self) -> None:
        raw = self.path_edit.text().strip()
        path = Path(raw) if raw else Path(default_piper_path())
        if not path.exists():
            QMessageBox.information(self, "Dọn dẹp", "Thư mục PiperTTS chưa tồn tại trên máy.")
            return
        cleaned_files = 0
        cleaned_bytes = 0
        for pattern in ("*.part", "*.tmp", "*.zip"):
            for f in path.rglob(pattern):
                if f.is_file():
                    try:
                        sz = f.stat().st_size
                        f.unlink()
                        cleaned_files += 1
                        cleaned_bytes += sz
                    except Exception:
                        pass
        mb = round(cleaned_bytes / (1024 * 1024), 2)
        msg = f"Đã dọn dẹp {cleaned_files} file tạm ({mb} MB)." if cleaned_files > 0 else "Không có file tạm nào cần dọn."
        reply = QMessageBox.question(
            self,
            "Dọn dẹp PiperTTS",
            f"{msg}\n\nBạn có muốn xoá TOÀN BỘ model PiperTTS đã tải để giải phóng dung lượng hoàn toàn không?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for pkg in PIPER_PACKAGES:
                self._delete_pkg_files(path, pkg)
            self.check_packages_status()
            self.changed.emit()
            QMessageBox.information(self, "Hoàn tất", "Đã dọn dẹp sạch toàn bộ model PiperTTS.")
        elif cleaned_files > 0:
            self.check_packages_status()
            self.changed.emit()
            QMessageBox.information(self, "Hoàn tất", f"Đã xoá {cleaned_files} file tạm.")

    def _download_required_clicked(self) -> None:
        required = [p for p in PIPER_PACKAGES if p.get("core")]
        self._start_download(required)

    def _start_download(self, pkgs: List[Dict[str, Any]]) -> None:
        if self._downloader is not None and self._downloader.isRunning():
            return
        root_path = self.path_edit.text().strip()
        if not root_path:
            paint_status(self.engine_status, "warning")
            self.engine_status.setText("Vui lòng chọn thư mục cài đặt PiperTTS trước khi tải.")
            return

        self.progress_container.setVisible(True)
        self.progress_bar.setValue(0)
        self.lbl_progress.setText("Đang khởi tạo kết nối tải PiperTTS…")
        self.btn_cancel_dl.setText("Hủy")
        self.btn_cancel_dl.setEnabled(True)
        self.btn_install.setEnabled(False)

        self._downloader = PiperDownloadWorker(root_path, pkgs, parent=self)
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
                f"background: {theme.SURFACE_RAISED}; color: {theme.BLUE}; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
        elif status_kind == "installed":
            w["status_badge"].setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.SUCCESS}; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
            w["btn_dl"].setText("Tải lại")
        elif status_kind == "error":
            w["status_badge"].setStyleSheet(
                f"background: {theme.SURFACE_RAISED}; color: {theme.DANGER}; "
                "border-radius: 8px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
            )
            w["btn_dl"].setText("Thử lại")

    def _on_download_finished(self, success: bool, message: str) -> None:
        errors = dict(getattr(self._downloader, "errors", {})) if self._downloader else {}
        self._downloader = None
        self.btn_install.setEnabled(True)
        self.btn_cancel_dl.setText("Đóng")
        self.btn_cancel_dl.setEnabled(True)
        self.lbl_progress.setText(message)
        paint_status(self.engine_status, "success" if success else "warning")
        self.engine_status.setText(message)
        self.check_packages_status(errors=errors)
        self.changed.emit()

    def _cancel_download_clicked(self) -> None:
        if self._downloader and self._downloader.isRunning():
            self._downloader.cancel()
            self.lbl_progress.setText("Đang dừng tiến trình tải…")
            self.btn_cancel_dl.setEnabled(False)
        else:
            self.progress_container.setVisible(False)

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"QFrame#PiperTTSCard {{ background-color: {theme.GROUP_BG}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 8px; }}"
        )
        if hasattr(self, "downloads_box"):
            self.downloads_box.setStyleSheet(
                f"QFrame#DownloadsBox {{ background-color: {theme.SURFACE_ALT}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 6px; }}"
            )
        if hasattr(self, "pkg_widgets"):
            for w in self.pkg_widgets.values():
                w["frame"].setStyleSheet(
                    f"QFrame#PkgRow {{ background-color: {theme.SURFACE}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 6px; }}"
                )


class _HFDownloadWorker(QThread):
    """Download HuggingFace repo files sequentially into a local directory."""

    progress = Signal(int, int, str)  # current, total, filename
    finished_all = Signal(bool, str)

    def __init__(self, target_dir: Path, jobs: List[Tuple[str, str, Optional[str]]], parent=None) -> None:
        super().__init__(parent)
        self._target_dir = Path(target_dir)
        self._jobs = list(jobs)  # (repo_id, filename, revision)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            from huggingface_hub import hf_hub_download
        except Exception as exc:
            self.finished_all.emit(False, f"Thiếu thư viện huggingface_hub: {exc}")
            return

        total = max(1, len(self._jobs))
        done = 0
        self._target_dir.mkdir(parents=True, exist_ok=True)
        for idx, (repo_id, filename, rev) in enumerate(self._jobs, 1):
            if self._cancel:
                self.finished_all.emit(False, f"Đã dừng tải ({done}/{total} file).")
                return
            self.progress.emit(idx - 1, total, filename)
            try:
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    revision=rev or None,
                    local_dir=str(self._target_dir),
                )
                done += 1
            except Exception as exc:
                self.finished_all.emit(False, f"Lỗi tải {filename}: {exc}")
                return
            self.progress.emit(idx, total, filename)
        self.finished_all.emit(True, f"Đã tải xong {done}/{total} file model.")


class HFModelTTSCard(QFrame):
    """Unified settings card for Supertonic 3 and Kokoro-82M (auto-discovery + HF download + status)."""

    changed = Signal()

    def __init__(self, engine_kind: str, parent=None) -> None:
        super().__init__(parent)
        self._kind = engine_kind  # "supertonic" | "kokoro"
        self._worker: Optional[_HFDownloadWorker] = None
        self.setObjectName("HFModelTTSCard")
        self._build_ui()
        self.apply_theme()

    def _build_ui(self) -> None:
        is_st = self._kind == "supertonic"
        title_txt = "Supertonic 3 (ONNX Đa ngôn ngữ)" if is_st else "Kokoro-82M (ONNX CPU / PyTorch GPU)"
        tag_txt = "44.1 kHz · 31 ngôn ngữ · CPU" if is_st else "24 kHz · 8 ngôn ngữ · CPU/GPU"
        sub_txt = (
            "Model ONNX thuần CPU chất lượng cao từ Supertone (10 giọng F1..F5, M1..M5; hỗ trợ vi, en, ja, ko, fr, es, pt, pl, ...).\n"
            "Tự động nhận model đã tải sẵn trong cit-voice-studio hoặc thư mục bên dưới."
            if is_st
            else "Model 82M siêu tự nhiên hỗ trợ EN (US/UK), FR, ES, PT, IT, HI, JA, ZH (chạy CPU ONNX hoặc tăng tốc GPU CUDA).\n"
            "Tự động nhận model đã tải sẵn trong cit-voice-studio hoặc thư mục bên dưới."
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel(title_txt)
        title.setStyleSheet("font-size: 13px; font-weight: bold;")
        hdr.addWidget(title)
        badge = QLabel(tag_txt)
        badge.setStyleSheet(
            f"background: {theme.SURFACE_RAISED}; color: {theme.ACCENT}; "
            "border-radius: 4px; padding: 2px 7px; font-size: 10px; font-weight: bold;"
        )
        hdr.addWidget(badge)
        hdr.addStretch(1)
        self.status_lbl = QLabel("Đang kiểm tra…")
        hdr.addWidget(self.status_lbl)
        root.addLayout(hdr)

        sub = QLabel(sub_txt)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        root.addWidget(sub)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        lbl_dir = QLabel("Thư mục:")
        lbl_dir.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        path_row.addWidget(lbl_dir)
        default_dir = default_supertonic_path() if is_st else default_kokoro_path()
        self.path_edit = QLineEdit(default_dir)
        self.path_edit.textChanged.connect(lambda: (self.check_status(), self.changed.emit()))
        path_row.addWidget(self.path_edit, 1)
        btn_browse = icon_button("folder", f"Chọn thư mục {title_txt}", role="muted", size=14, side=28)
        btn_browse.clicked.connect(self._browse_dir)
        path_row.addWidget(btn_browse)
        btn_scan = QPushButton("Quét lại")
        btn_scan.setFixedHeight(28)
        btn_scan.clicked.connect(self.check_status)
        path_row.addWidget(btn_scan)
        root.addLayout(path_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        if is_st:
            self.btn_dl_main = QPushButton("Tải Supertonic 3 (~260 MB)")
            self.btn_dl_main.clicked.connect(self._download_supertonic)
            btn_row.addWidget(self.btn_dl_main)
        else:
            self.btn_dl_main = QPushButton("Tải Kokoro Core ONNX (~340 MB)")
            self.btn_dl_main.clicked.connect(lambda: self._download_kokoro_pack("core"))
            btn_row.addWidget(self.btn_dl_main)

            self.btn_dl_ja = QPushButton("+ Pack JA")
            self.btn_dl_ja.clicked.connect(lambda: self._download_kokoro_pack("ja"))
            btn_row.addWidget(self.btn_dl_ja)

            self.btn_dl_zh = QPushButton("+ Pack ZH")
            self.btn_dl_zh.clicked.connect(lambda: self._download_kokoro_pack("zh"))
            btn_row.addWidget(self.btn_dl_zh)

            self.btn_dl_gpu = QPushButton("+ PyTorch GPU (~330 MB)")
            self.btn_dl_gpu.clicked.connect(lambda: self._download_kokoro_pack("torch"))
            btn_row.addWidget(self.btn_dl_gpu)

        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.progress_lbl.setVisible(False)
        root.addWidget(self.progress_lbl)

    def _browse_dir(self) -> None:
        start = self.path_edit.text().strip() or str(ROOT)
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu model", start)
        if chosen:
            self.path_edit.setText(chosen.replace("\\", "/"))

    def _engine(self):
        cfg = {"paths": {self._kind: self.path_edit.text().strip()}}
        if self._kind == "supertonic":
            from core.supertonic_tts_engine import SupertonicTTSEngine

            return SupertonicTTSEngine(cfg)
        from core.kokoro_tts_engine import KokoroTTSEngine

        return KokoroTTSEngine(cfg)

    def check_status(self) -> None:
        eng = self._engine()
        ok, msg = eng.check_ready()
        if self._kind == "kokoro" and ok:
            torch_root = eng.resolve_torch_root()
            gpu_note = " · GPU PyTorch sẵn sàng" if torch_root is not None else " · CPU ONNX"
            msg = msg + gpu_note
        self.status_lbl.setText(f"● {msg}")
        paint_status(self.status_lbl, "success" if ok else "warning")

    def _start_jobs(self, jobs: List[Tuple[str, str, Optional[str]]]) -> None:
        if self._worker and self._worker.isRunning():
            return
        raw = self.path_edit.text().strip()
        target = Path(
            normalize_supertonic_path(raw) if self._kind == "supertonic" else normalize_kokoro_path(raw)
        )
        self.progress_bar.setRange(0, max(1, len(jobs)))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.progress_lbl.setText("Đang chuẩn bị tải từ Hugging Face…")
        self.progress_lbl.setVisible(True)
        self.btn_dl_main.setEnabled(False)

        self._worker = _HFDownloadWorker(target, jobs, parent=self)
        self._worker.progress.connect(
            lambda cur, tot, fname: (
                self.progress_bar.setValue(cur),
                self.progress_lbl.setText(f"[{cur}/{tot}] {fname}"),
            )
        )
        self._worker.finished_all.connect(self._on_download_done)
        self._worker.start()

    def _on_download_done(self, ok: bool, msg: str) -> None:
        self._worker = None
        self.btn_dl_main.setEnabled(True)
        self.progress_lbl.setText(msg)
        self.check_status()
        self.changed.emit()

    def _download_supertonic(self) -> None:
        from core.supertonic_tts_engine import SUPERTONIC_ALL_FILES, SUPERTONIC_REPO, SUPERTONIC_REV

        jobs = [(SUPERTONIC_REPO, f, SUPERTONIC_REV) for f in SUPERTONIC_ALL_FILES]
        self._start_jobs(jobs)

    def _download_kokoro_pack(self, pack: str) -> None:
        from core.kokoro_tts_engine import (
            KOKORO_JA_VOICES,
            KOKORO_ONNX_REPO,
            KOKORO_ONNX_REV,
            KOKORO_TORCH_REPO,
            KOKORO_TORCH_REV,
            KOKORO_ZH_VOICES,
        )

        if pack == "ja":
            jobs = [(KOKORO_ONNX_REPO, f"voices/{v}.bin", KOKORO_ONNX_REV) for v in KOKORO_JA_VOICES]
        elif pack == "zh":
            jobs = [(KOKORO_ONNX_REPO, f"voices/{v}.bin", KOKORO_ONNX_REV) for v in KOKORO_ZH_VOICES]
        elif pack == "torch":
            jobs = [
                (KOKORO_TORCH_REPO, "config.json", KOKORO_TORCH_REV),
                (KOKORO_TORCH_REPO, "kokoro-v1_0.pth", KOKORO_TORCH_REV),
            ]
        else:
            core_voices = ("af_heart", "af_bella", "am_adam", "bf_emma", "bm_george", "ef_dora", "ff_siwis", "if_sara", "pf_dora", "hf_alpha")
            jobs = [(KOKORO_ONNX_REPO, "onnx/model.onnx", KOKORO_ONNX_REV)] + [
                (KOKORO_ONNX_REPO, f"voices/{v}.bin", KOKORO_ONNX_REV) for v in core_voices
            ]
        self._start_jobs(jobs)

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"QFrame#HFModelTTSCard {{ background-color: {theme.GROUP_BG}; border: 1px solid {theme.GROUP_BORDER}; border-radius: 8px; }}"
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

        # PiperTTS path
        piper_row = QHBoxLayout()
        self.piper_edit = QLineEdit(default_piper_path())
        btn_piper = icon_button("folder", "Chọn thư mục PiperTTS", role="muted", size=14, side=30)
        btn_piper.clicked.connect(lambda: self._browse_dir(self.piper_edit, "Chọn thư mục PiperTTS"))
        piper_row.addWidget(self.piper_edit, 1)
        piper_row.addWidget(btn_piper)
        paths_form.addRow("Thư mục PiperTTS", self._wrap(piper_row))

        # Supertonic 3 path
        st_row = QHBoxLayout()
        self.supertonic_edit = QLineEdit(default_supertonic_path())
        btn_st = icon_button("folder", "Chọn thư mục Supertonic 3", role="muted", size=14, side=30)
        btn_st.clicked.connect(lambda: self._browse_dir(self.supertonic_edit, "Chọn thư mục Supertonic 3"))
        st_row.addWidget(self.supertonic_edit, 1)
        st_row.addWidget(btn_st)
        paths_form.addRow("Thư mục Supertonic 3", self._wrap(st_row))

        # Kokoro-82M path
        kk_row = QHBoxLayout()
        self.kokoro_edit = QLineEdit(default_kokoro_path())
        btn_kk = icon_button("folder", "Chọn thư mục Kokoro-82M", role="muted", size=14, side=30)
        btn_kk.clicked.connect(lambda: self._browse_dir(self.kokoro_edit, "Chọn thư mục Kokoro-82M"))
        kk_row.addWidget(self.kokoro_edit, 1)
        kk_row.addWidget(btn_kk)
        paths_form.addRow("Thư mục Kokoro-82M", self._wrap(kk_row))



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
            chosen = str(Path(chosen).resolve().as_posix())
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

        # Two-way sync of credentials between Cloudflare Text and Image cards
        def _sync_cf_from_text() -> None:
            acc = self.card_cloudflare_text.get_account_id()
            tok = self.card_cloudflare_text.get_token()
            if self.card_cloudflare_image.get_account_id() != acc:
                self.card_cloudflare_image.account_edit.blockSignals(True)
                self.card_cloudflare_image.set_account_id(acc)
                self.card_cloudflare_image.account_edit.blockSignals(False)
            if self.card_cloudflare_image.get_token() != tok:
                self.card_cloudflare_image.token_edit.blockSignals(True)
                self.card_cloudflare_image.set_token(tok)
                self.card_cloudflare_image.token_edit.blockSignals(False)

        def _sync_cf_from_image() -> None:
            acc = self.card_cloudflare_image.get_account_id()
            tok = self.card_cloudflare_image.get_token()
            if self.card_cloudflare_text.get_account_id() != acc:
                self.card_cloudflare_text.account_edit.blockSignals(True)
                self.card_cloudflare_text.set_account_id(acc)
                self.card_cloudflare_text.account_edit.blockSignals(False)
            if self.card_cloudflare_text.get_token() != tok:
                self.card_cloudflare_text.token_edit.blockSignals(True)
                self.card_cloudflare_text.set_token(tok)
                self.card_cloudflare_text.token_edit.blockSignals(False)

        self.card_cloudflare_text.account_edit.textChanged.connect(_sync_cf_from_text)
        self.card_cloudflare_text.token_edit.textChanged.connect(_sync_cf_from_text)
        self.card_cloudflare_image.account_edit.textChanged.connect(_sync_cf_from_image)
        self.card_cloudflare_image.token_edit.textChanged.connect(_sync_cf_from_image)

        # Two-way sync between General page and H2MTTS card path inputs
        def _sync_h2_from_card(text: str) -> None:
            if self.general_page.h2mtts_edit.text() != text:
                self.general_page.h2mtts_edit.blockSignals(True)
                self.general_page.h2mtts_edit.setText(text)
                self.general_page.h2mtts_edit.blockSignals(False)

        def _sync_h2_from_general(text: str) -> None:
            if self.card_h2mtts.path_edit.text() != text:
                self.card_h2mtts.path_edit.blockSignals(True)
                self.card_h2mtts.path_edit.setText(text)
                self.card_h2mtts.path_edit.blockSignals(False)
                self.card_h2mtts.detect_specs()
                self.card_h2mtts.check_packages_status()

        self.card_h2mtts.path_edit.textChanged.connect(_sync_h2_from_card)
        self.general_page.h2mtts_edit.textChanged.connect(_sync_h2_from_general)

        # Two-way sync between General page and PiperTTS card path inputs
        def _sync_piper_from_card(text: str) -> None:
            if self.general_page.piper_edit.text() != text:
                self.general_page.piper_edit.blockSignals(True)
                self.general_page.piper_edit.setText(text)
                self.general_page.piper_edit.blockSignals(False)

        def _sync_piper_from_general(text: str) -> None:
            if self.card_piper.path_edit.text() != text:
                self.card_piper.path_edit.blockSignals(True)
                self.card_piper.path_edit.setText(text)
                self.card_piper.path_edit.blockSignals(False)
                self.card_piper.check_packages_status()

        self.card_piper.path_edit.textChanged.connect(_sync_piper_from_card)
        self.general_page.piper_edit.textChanged.connect(_sync_piper_from_general)

        def _bind_two_way(card: HFModelTTSCard, edit: QLineEdit) -> None:
            def _from_card(text: str) -> None:
                if edit.text() != text:
                    edit.blockSignals(True)
                    edit.setText(text)
                    edit.blockSignals(False)

            def _from_gen(text: str) -> None:
                if card.path_edit.text() != text:
                    card.path_edit.blockSignals(True)
                    card.path_edit.setText(text)
                    card.path_edit.blockSignals(False)
                    card.check_status()

            card.path_edit.textChanged.connect(_from_card)
            edit.textChanged.connect(_from_gen)

        _bind_two_way(self.card_supertonic, self.general_page.supertonic_edit)
        _bind_two_way(self.card_kokoro, self.general_page.kokoro_edit)



        self.inner_tabs.addTab(api_wrapper, "API")

    # ── Page 1: Văn bản ──────────────────────────────────────────────────────
    def _build_text_ai_page(self) -> None:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "Văn bản",
                "Viết kịch bản, caption, tạo nội dung bằng Gemini Flash hoặc Cloudflare Workers AI.",
            )
        )

        self.card_gemini = ServiceCard(
            service_id="gemini",
            title="Gemini",
            tag="",
            key_placeholder="Nhập API key Gemini (AIza...)",
            models=GEMINI_TEXT_MODELS,
            has_endpoint=False,
            has_test_model=False,
            parent=page,
        )
        self.card_gemini.changed.connect(self._sync_shared_keys)
        self.card_gemini.changed.connect(self._on_changed)
        self.card_gemini.save_requested.connect(lambda sid, k: self._on_changed())
        layout.addWidget(self.card_gemini)

        self.card_cloudflare_text = CloudflareCard(
            service_id="cloudflare_text",
            title="Cloudflare Worker AI",
            tag="",
            subtitle="",
            task="Text Generation",
            models=CLOUDFLARE_TEXT_MODELS,
            default_model=DEFAULT_CF_TEXT_MODEL,
            parent=page,
        )
        self.card_cloudflare_text.changed.connect(self._on_changed)
        self.card_cloudflare_text.save_requested.connect(lambda sid, acc, tok: self._on_changed())
        layout.addWidget(self.card_cloudflare_text)

        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Văn bản")

    # ── Page 2: Giọng đọc ────────────────────────────────────────────────────
    def _build_tts_page(self) -> None:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "Giọng đọc",
                "Thuyết minh đa ngôn ngữ cho video với Edge-TTS, H2MTTS, PiperTTS, Supertonic 3 và Kokoro-82M offline.",
            )
        )

        # Edge-TTS (Đa ngôn ngữ & Miễn phí)
        self.card_edge_tts = ServiceCard(
            service_id="edge_tts",
            title="Edge-TTS (Đa ngôn ngữ & Miễn phí)",
            tag="Miễn phí · Đa ngôn ngữ",
            subtitle=(
                "Miễn phí – không cần API key, không giới hạn ký tự.\n"
                "Hỗ trợ đa ngôn ngữ: Tiếng Việt (Hoài My, Nam Minh), Tiếng Anh, Tiếng Trung, "
                "Tiếng Nhật, Tiếng Hàn, Tiếng Pháp, Tiếng Tây Ban Nha, Tiếng Thái..."
            ),
            models=EDGE_MULTILANG_VOICES,
            has_key_input=False,
            parent=page,
        )
        self.card_edge_tts.switch.setChecked(True)
        self.card_edge_tts.set_status("valid", "• Hợp lệ")
        self.card_edge_tts.changed.connect(self._on_changed)
        layout.addWidget(self.card_edge_tts)

        # Local H2MTTS (Nâng cao)
        self.card_h2mtts = H2MTTSCard(parent=page)
        self.card_h2mtts.changed.connect(self._on_changed)
        layout.addWidget(self.card_h2mtts)

        # Local PiperTTS (Đa ngôn ngữ · Siêu nhẹ)
        self.card_piper = PiperTTSCard(parent=page)
        self.card_piper.changed.connect(self._on_changed)
        layout.addWidget(self.card_piper)

        # Local Supertonic 3 & Kokoro-82M (CIT Voice Studio models)
        self.card_supertonic = HFModelTTSCard("supertonic", parent=page)
        self.card_supertonic.changed.connect(self._on_changed)
        layout.addWidget(self.card_supertonic)

        self.card_kokoro = HFModelTTSCard("kokoro", parent=page)
        self.card_kokoro.changed.connect(self._on_changed)
        layout.addWidget(self.card_kokoro)



        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Giọng đọc")

    # ── Page 3: Ảnh ──────────────────────────────────────────────────────────
    def _build_media_ai_page(self) -> None:
        page = QWidget()
        page.setObjectName("PageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "Ảnh",
                "Tạo ảnh minh họa & thumbnail bằng Cloudflare Workers AI (Flux, Leonardo, SDXL...).",
            )
        )

        self.card_cloudflare_image = CloudflareCard(
            service_id="cloudflare_image",
            title="Cloudflare Worker AI",
            tag="",
            subtitle="",
            task="Text-to-Image",
            models=CLOUDFLARE_IMAGE_MODELS,
            default_model=DEFAULT_CF_IMAGE_MODEL,
            parent=page,
        )
        self.card_cloudflare_image.changed.connect(self._on_changed)
        self.card_cloudflare_image.save_requested.connect(lambda sid, acc, tok: self._on_changed())
        layout.addWidget(self.card_cloudflare_image)

        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Ảnh")

    # ── Shared key propagation ───────────────────────────────────────────────
    def _sync_shared_keys(self) -> None:
        if hasattr(self, "card_cloudflare_text") and hasattr(self, "card_cloudflare_image"):
            acc = self.card_cloudflare_image.get_account_id() or self.card_cloudflare_text.get_account_id()
            tok = self.card_cloudflare_image.get_token() or self.card_cloudflare_text.get_token()
            if acc:
                if self.card_cloudflare_text.get_account_id() != acc:
                    self.card_cloudflare_text.set_account_id(acc)
                if self.card_cloudflare_image.get_account_id() != acc:
                    self.card_cloudflare_image.set_account_id(acc)
            if tok:
                if self.card_cloudflare_text.get_token() != tok:
                    self.card_cloudflare_text.set_token(tok)
                if self.card_cloudflare_image.get_token() != tok:
                    self.card_cloudflare_image.set_token(tok)

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
            h2 = normalize_h2mtts_path(raw_h2)
            self.general_page.h2mtts_edit.setText(h2)
            self.card_h2mtts.path_edit.setText(h2)
            self.card_h2mtts.detect_specs()

            raw_piper = str(paths.get("piper") or "").strip()
            piper_norm = normalize_piper_path(raw_piper)
            self.general_page.piper_edit.setText(piper_norm)
            self.card_piper.path_edit.setText(piper_norm)
            self.card_piper.check_packages_status()

            st_norm = normalize_supertonic_path(str(paths.get("supertonic") or "").strip())
            self.general_page.supertonic_edit.setText(st_norm)
            self.card_supertonic.path_edit.setText(st_norm)
            self.card_supertonic.check_status()

            kk_norm = normalize_kokoro_path(str(paths.get("kokoro") or "").strip())
            self.general_page.kokoro_edit.setText(kk_norm)
            self.card_kokoro.path_edit.setText(kk_norm)
            self.card_kokoro.check_status()



            self.general_page.ffmpeg_edit.setText(str(paths.get("ffmpeg") or "ffmpeg"))
            self.general_page.ffprobe_edit.setText(str(paths.get("ffprobe") or "ffprobe"))
            self.general_page.chrome_edit.setText(str(settings.get("youtube", {}).get("browser_profile") or ""))
            self.general_page.retry_spin.setValue(int(app.get("retry", 1) or 1))
            self.general_page.stop_error_check.setChecked(bool(app.get("stop_on_error", False)))
            self.general_page.log_dir_edit.setText(str(app.get("log_dir") or "logs"))

            # Gemini Flash key sync
            gemini_val = str(api.get("gemini_key") or script.get("api_key") or video.get("ai_api_key") or "").strip()
            self.card_gemini.key_edit.setText(gemini_val)

            gemini_enabled = bool(api.get("gemini_enabled", bool(gemini_val)))
            self.card_gemini.switch.blockSignals(True)
            self.card_gemini.switch.setChecked(gemini_enabled and bool(gemini_val))
            self.card_gemini.switch.blockSignals(False)

            if gemini_val:
                if gemini_enabled:
                    self.card_gemini.set_status("valid", "• Đã kích hoạt")
                else:
                    self.card_gemini.set_status("needed", "• Đang tắt")
            else:
                self.card_gemini.set_status("missing", "• Chưa kích hoạt / Thiếu key")

            from core.ai_client import normalize_model
            gemini_model = normalize_model(str(script.get("model") or api.get("gemini_model") or ""))
            if gemini_model:
                self.card_gemini.set_model(gemini_model)

            # Cloudflare credentials
            cf_account = str(api.get("cloudflare_account_id") or DEFAULT_CF_ACCOUNT_ID).strip()
            cf_token = str(api.get("cloudflare_token") or DEFAULT_CF_API_TOKEN).strip()
            cf_text_model = str(api.get("cloudflare_text_model") or DEFAULT_CF_TEXT_MODEL).strip()
            cf_image_model = str(video.get("ai_image_model") or api.get("cloudflare_image_model") or DEFAULT_CF_IMAGE_MODEL).strip()

            self.card_cloudflare_text.set_account_id(cf_account)
            self.card_cloudflare_text.set_token(cf_token)
            self.card_cloudflare_text.set_model(cf_text_model)
            cf_text_en = bool(api.get("cloudflare_text_enabled", True))
            self.card_cloudflare_text.switch.blockSignals(True)
            self.card_cloudflare_text.switch.setChecked(cf_text_en)
            self.card_cloudflare_text.switch.blockSignals(False)
            if cf_account and cf_token and cf_text_en:
                self.card_cloudflare_text.set_status("valid", "• Đã kích hoạt")

            self.card_cloudflare_image.set_account_id(cf_account)
            self.card_cloudflare_image.set_token(cf_token)
            self.card_cloudflare_image.set_model(cf_image_model)
            cf_img_en = bool(api.get("cloudflare_image_enabled", True))
            self.card_cloudflare_image.switch.blockSignals(True)
            self.card_cloudflare_image.switch.setChecked(cf_img_en)
            self.card_cloudflare_image.switch.blockSignals(False)
            if cf_account and cf_token and cf_img_en:
                self.card_cloudflare_image.set_status("valid", "• Đã kích hoạt")

            # Edge-TTS
            self.card_edge_tts.switch.blockSignals(True)
            self.card_edge_tts.switch.setChecked(bool(api.get("edge_tts_enabled", True)))
            self.card_edge_tts.switch.blockSignals(False)
            edge_v = str(api.get("edge_tts_voice") or "").strip()
            if edge_v:
                self.card_edge_tts.set_model(edge_v)

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
        h2_norm = normalize_h2mtts_path(h2)
        try:
            h2_p = Path(h2_norm).resolve()
            root_p = ROOT.resolve()
            if h2_p == (root_p / "H2MTTS").resolve():
                paths["h2mtts"] = "H2MTTS"
            elif h2_p == (root_p / "tts" / "H2MTTS").resolve():
                paths["h2mtts"] = "tts/H2MTTS"
            else:
                paths["h2mtts"] = h2_norm
        except Exception:
            paths["h2mtts"] = h2_norm

        piper_val = self.card_piper.path_edit.text().strip() or self.general_page.piper_edit.text().strip()
        piper_norm = normalize_piper_path(piper_val)
        try:
            piper_p = Path(piper_norm).resolve()
            root_p = ROOT.resolve()
            if piper_p == (root_p / "PiperTTS").resolve():
                paths["piper"] = "PiperTTS"
            elif piper_p == (root_p / "tts" / "PiperTTS").resolve():
                paths["piper"] = "tts/PiperTTS"
            else:
                paths["piper"] = piper_norm
        except Exception:
            paths["piper"] = piper_norm

        def _save_rel_path(key: str, raw_val: str, norm_fn, folder_name: str) -> None:
            norm = norm_fn(raw_val)
            try:
                p_res = Path(norm).resolve()
                root_p = ROOT.resolve()
                if p_res == (root_p / folder_name).resolve():
                    paths[key] = folder_name
                elif p_res == (root_p / "tts" / folder_name).resolve():
                    paths[key] = f"tts/{folder_name}"
                else:
                    paths[key] = norm
            except Exception:
                paths[key] = norm

        _save_rel_path(
            "supertonic",
            self.card_supertonic.path_edit.text().strip() or self.general_page.supertonic_edit.text().strip(),
            normalize_supertonic_path,
            "SupertonicTTS",
        )
        _save_rel_path(
            "kokoro",
            self.card_kokoro.path_edit.text().strip() or self.general_page.kokoro_edit.text().strip(),
            normalize_kokoro_path,
            "KokoroTTS",
        )



        paths["ffmpeg"] = self.general_page.ffmpeg_edit.text().strip() or "ffmpeg"
        paths["ffprobe"] = self.general_page.ffprobe_edit.text().strip() or "ffprobe"

        # App options
        app["retry"] = int(self.general_page.retry_spin.value())
        app["stop_on_error"] = self.general_page.stop_error_check.isChecked()
        app["log_dir"] = self.general_page.log_dir_edit.text().strip() or "logs"

        # Gemini Flash
        gemini_k = self.card_gemini.get_key()
        gemini_en = self.card_gemini.switch.isChecked()
        api["gemini_key"] = gemini_k
        api["gemini_enabled"] = gemini_en
        if gemini_k:
            script["api_key"] = gemini_k
        else:
            script["api_key"] = ""

        api["gemini_model"] = self.card_gemini.get_model()
        script["model"] = self.card_gemini.get_model()

        # Cloudflare settings
        cf_acc = self.card_cloudflare_image.get_account_id() or self.card_cloudflare_text.get_account_id()
        cf_tok = self.card_cloudflare_image.get_token() or self.card_cloudflare_text.get_token()
        api["cloudflare_account_id"] = cf_acc
        api["cloudflare_token"] = cf_tok
        api["cloudflare_text_model"] = self.card_cloudflare_text.get_model()
        api["cloudflare_text_enabled"] = self.card_cloudflare_text.switch.isChecked()
        api["cloudflare_image_model"] = self.card_cloudflare_image.get_model()
        api["cloudflare_image_enabled"] = self.card_cloudflare_image.switch.isChecked()
        video["ai_image_model"] = self.card_cloudflare_image.get_model()

        # Edge-TTS
        api["edge_tts_enabled"] = self.card_edge_tts.switch.isChecked()
        api["edge_tts_voice"] = self.card_edge_tts.get_model()

        self._settings = settings

    def apply_theme(self) -> None:
        if hasattr(self, "card_gemini"):
            self.card_gemini.apply_theme()
        if hasattr(self, "card_cloudflare_text"):
            self.card_cloudflare_text.apply_theme()
        if hasattr(self, "card_edge_tts"):
            self.card_edge_tts.apply_theme()
        if hasattr(self, "card_h2mtts"):
            self.card_h2mtts.apply_theme()
        if hasattr(self, "card_piper"):
            self.card_piper.apply_theme()
        if hasattr(self, "card_supertonic"):
            self.card_supertonic.apply_theme()
        if hasattr(self, "card_kokoro"):
            self.card_kokoro.apply_theme()

        if hasattr(self, "card_cloudflare_image"):
            self.card_cloudflare_image.apply_theme()


    def set_pipeline_busy(self, busy: bool) -> None:
        self._pipeline_busy = bool(busy)
        for btn in self.findChildren(QPushButton):
            btn.setEnabled(not busy)
        for edit in self.findChildren(QLineEdit):
            edit.setReadOnly(busy)
        for combo in self.findChildren(QComboBox):
            combo.setEnabled(not busy)
        for spin in self.findChildren(QSpinBox):
            spin.setEnabled(not busy)
        for chk in self.findChildren(QCheckBox):
            chk.setEnabled(not busy)
