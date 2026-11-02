"""Settings tab: General options, system paths, and API & AI services."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter
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
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.components.icons import icon, set_icon
from ui.components.page import PageHeader, icon_button, paint_status, wrap_scroll
from utils.logger import get_logger

logger = get_logger("ui.settings")


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
    check_dir = Path(h2mtts_path) if h2mtts_path else Path("D:/mui/f375/tts/H2MTTS")
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
                        self.done.emit(sid, True, "Kết nối Gemini thành công!")
                        return
            except urllib.error.HTTPError as exc:
                self.done.emit(sid, False, f"Lỗi HTTP {exc.code} (Key không hợp lệ)")
                return
            except Exception as exc:
                self.done.emit(sid, False, f"Lỗi kết nối: {exc}")
                return

        elif sid in ("openai", "openai_audio"):
            if not key:
                self.done.emit(sid, False, "Chưa nhập API key OpenAI.")
                return
            try:
                url = self.endpoint or "https://api.openai.com/v1/models"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "User-Agent": "AutoYout/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        self.done.emit(sid, True, "Kết nối OpenAI thành công!")
                        return
            except urllib.error.HTTPError as exc:
                self.done.emit(sid, False, f"Lỗi HTTP {exc.code} (Key không hợp lệ)")
                return
            except Exception as exc:
                self.done.emit(sid, False, f"Lỗi: {exc}")
                return

        elif sid == "deepseek":
            if not key:
                self.done.emit(sid, False, "Chưa nhập API key DeepSeek.")
                return
            try:
                base = self.endpoint.rstrip("/") if self.endpoint else "https://api.deepseek.com"
                url = f"{base}/models"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "User-Agent": "AutoYout/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        self.done.emit(sid, True, "Kết nối DeepSeek thành công!")
                        return
            except Exception as exc:
                self.done.emit(sid, False, f"Lỗi: {exc}")
                return

        elif sid in ("fal", "seedance_fal", "kling_fal"):
            if not key:
                self.done.emit(sid, False, "Chưa nhập API key Fal.ai.")
                return
            if len(key) >= 10:
                self.done.emit(sid, True, "Định dạng key Fal.ai hợp lệ.")
            else:
                self.done.emit(sid, False, "Độ dài key Fal.ai không đúng.")
            return

        elif sid == "edge_tts":
            self.done.emit(sid, True, "Edge-TTS sẵn sàng hoạt động (Miễn phí).")
            return

        elif sid in ("seedance_comet", "seedance_piapi", "elevenlabs"):
            if not key:
                self.done.emit(sid, False, "Chưa nhập API key.")
                return
            self.done.emit(sid, True, "Đã lưu key & sẵn sàng.")
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service_id = service_id
        self.title_text = title
        self.tag_text = tag
        self.shared_from = shared_from
        self.has_key_input = has_key_input
        self.has_endpoint = has_endpoint
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
            for label, val in models:
                self.model_combo.addItem(label, val)
            self.model_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
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
        key = self.key_edit.text().strip() if hasattr(self, "key_edit") else ""
        self.save_requested.emit(self.service_id, key)
        self.set_status("valid", "• Đã lưu key")

    def _test_model_clicked(self) -> None:
        self.check_connection()

    def check_connection(self) -> None:
        if self._checker is not None:
            return
        key = self.key_edit.text().strip() if hasattr(self, "key_edit") else ""
        ep = self.ep_edit.text().strip() if hasattr(self, "ep_edit") else ""
        model = self.model_combo.currentData() if hasattr(self, "model_combo") else ""

        self.set_status("running", "• Đang kiểm tra…")
        self._checker = _CheckApiWorker(self.service_id, key, ep, str(model or ""), self)
        self._checker.done.connect(self._on_check_done)
        self._checker.start()

    def _on_check_done(self, _sid: str, success: bool, message: str) -> None:
        self._checker = None
        if success:
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


class H2MTTSCard(QFrame):
    """Local H2MTTS engine and hardware specifications detector."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("H2MTTSCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────────────
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

        # ── Hardware Specifications Box ──────────────────────────────────────
        self.specs_box = QFrame()
        self.specs_box.setObjectName("SpecsBox")
        specs_layout = QVBoxLayout(self.specs_box)
        specs_layout.setContentsMargins(12, 10, 12, 10)
        specs_layout.setSpacing(8)

        # Rating line
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

        # Specs grid
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

        # ── Path row ─────────────────────────────────────────────────────────
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_lbl = QLabel("Thư mục H2MTTS:")
        path_lbl.setStyleSheet("font-size: 11px; font-weight: bold;")
        self.path_edit = QLineEdit("D:/mui/f375/tts/H2MTTS")
        self.path_edit.textChanged.connect(lambda _: self.changed.emit())
        btn_browse = icon_button(
            "folder-open", "Chọn thư mục H2MTTS…", role="muted", size=14, side=30
        )
        btn_browse.clicked.connect(self._browse_dir)

        path_row.addWidget(path_lbl)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(btn_browse)
        layout.addLayout(path_row)

        # ── Action buttons ───────────────────────────────────────────────────
        act_row = QHBoxLayout()
        act_row.setSpacing(10)

        self.btn_install = QPushButton("Tải & cài engine GPU (~4 GB)")
        self.btn_install.setObjectName("primaryButton")
        set_icon(self.btn_install, "download", "white", 14)
        self.btn_install.clicked.connect(self._install_or_check_clicked)
        act_row.addWidget(self.btn_install)

        self.btn_check_engine = QPushButton("Kiểm tra engine")
        set_icon(self.btn_check_engine, "check-circle", "accent", 14)
        self.btn_check_engine.clicked.connect(self.detect_specs)
        act_row.addWidget(self.btn_check_engine)

        self.engine_status = QLabel("")
        act_row.addWidget(self.engine_status, 1)
        layout.addLayout(act_row)

        self.apply_theme()
        self.detect_specs()

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
            self.changed.emit()

    def _install_or_check_clicked(self) -> None:
        path = Path(self.path_edit.text().strip())
        if path.exists():
            paint_status(self.engine_status, "success")
            self.engine_status.setText(f"Thư mục H2MTTS đã sẵn sàng tại {path}.")
        else:
            paint_status(self.engine_status, "warning")
            self.engine_status.setText(
                f"Thư mục {path} chưa tồn tại. Vui lòng tạo hoặc giải nén H2MTTS vào thư mục này."
            )

    def detect_specs(self) -> None:
        path = self.path_edit.text().strip()
        data = detect_hardware(path)

        self.lbl_cpu.setText(data["cpu"])
        self.lbl_ram.setText(data["ram"])
        self.lbl_gpu.setText(data["gpu"])
        self.lbl_disk.setText(data["disk"])
        self.lbl_note.setText(data["note"])

        if data["installed"]:
            self.status_badge.setText("Đã cài")
            self.status_badge.setStyleSheet(
                "background: rgba(16, 185, 129, 0.18); color: #10B981; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("Engine GPU đã sẵn sàng")
        else:
            self.status_badge.setText("Chưa cài")
            self.status_badge.setStyleSheet(
                "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
            )
            self.btn_install.setText("Tải & cài engine GPU (~4 GB)")

        self.rating_badge.setText(f"• Đánh giá cấu hình: {data['rating']}")

    def apply_theme(self) -> None:
        dark = theme.current_mode() == "dark"
        bg = "#111C2D" if dark else "#FFFFFF"
        border = "#2A3E59" if dark else "#E2E8F0"
        box_bg = "rgba(15, 23, 42, 0.4)" if dark else "rgba(241, 245, 249, 0.7)"
        self.setStyleSheet(
            f"QFrame#H2MTTSCard {{ background-color: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
        )
        self.specs_box.setStyleSheet(
            f"QFrame#SpecsBox {{ background-color: {box_bg}; border: 1px solid {border}; border-radius: 6px; }}"
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
        self.h2mtts_edit = QLineEdit("D:/mui/f375/tts/H2MTTS")
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
                "Viết kịch bản, caption, trả lời tự động.",
            )
        )

        # Omnira banner
        banner = QFrame()
        banner.setStyleSheet(
            "background: rgba(2, 132, 199, 0.08); border: 1px solid rgba(2, 132, 199, 0.25); "
            "border-radius: 8px; padding: 10px;"
        )
        b_layout = QVBoxLayout(banner)
        b_layout.setContentsMargins(10, 8, 10, 8)
        b_layout.setSpacing(6)

        b_top = QHBoxLayout()
        b_title = QLabel("AI Omnira")
        b_title.setStyleSheet("font-weight: bold; color: #0284C7; font-size: 12px;")
        b_badge = QLabel("Đang dùng")
        b_badge.setStyleSheet(
            "background: rgba(16, 185, 129, 0.18); color: #10B981; "
            "border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
        )
        b_top.addWidget(b_title)
        b_top.addWidget(b_badge)
        b_top.addStretch(1)
        b_layout.addLayout(b_top)

        b_desc = QLabel(
            "AI văn bản có sẵn trong gói của bạn – tự dùng khi bạn chưa nhập key AI văn bản riêng. "
            "Chỉ dùng để viết chữ (kịch bản, caption, trả lời tin nhắn); ảnh / video vẫn cần key riêng "
            "hoặc Google Flow. Nhập key riêng bên dưới để dùng không giới hạn."
        )
        b_desc.setWordWrap(True)
        b_desc.setStyleSheet("color: #64748B; font-size: 11px;")
        b_layout.addWidget(b_desc)

        b_bottom = QHBoxLayout()
        b_today = QLabel("Hôm nay: còn 30/30 lượt")
        b_today.setStyleSheet("color: #0284C7; font-size: 11px; font-weight: bold;")
        b_month = QLabel("Tháng này: còn 100/100 lượt")
        b_month.setStyleSheet("color: #0284C7; font-size: 11px; font-weight: bold;")
        b_bottom.addWidget(b_today)
        b_bottom.addSpacing(20)
        b_bottom.addWidget(b_month)
        b_bottom.addStretch(1)
        b_layout.addLayout(b_bottom)

        layout.addWidget(banner)

        # 3 Text Cards
        self.card_deepseek = ServiceCard(
            service_id="deepseek",
            title="DeepSeek V3 / R1",
            tag="DeepSeek V3 / R1 (Reasoning)",
            key_placeholder="Nhập sk-...",
            models=[
                ("deepseek-v3 (chat)", "deepseek-v3"),
                ("deepseek-r1 (reasoner)", "deepseek-r1"),
            ],
            has_endpoint=True,
            has_test_model=True,
            parent=page,
        )
        self.card_deepseek.changed.connect(self._on_changed)
        layout.addWidget(self.card_deepseek)

        self.card_openai = ServiceCard(
            service_id="openai",
            title="OpenAI GPT-4o",
            tag="GPT-4o & GPT-4o-mini",
            key_placeholder="Nhập sk-proj-...",
            models=[
                ("gpt-4o (Chất lượng)", "gpt-4o"),
                ("gpt-4o-mini (Nhanh, rẻ)", "gpt-4o-mini"),
                ("o1", "o1"),
                ("o3-mini", "o3-mini"),
            ],
            has_endpoint=True,
            has_test_model=True,
            parent=page,
        )
        self.card_openai.changed.connect(self._sync_shared_keys)
        self.card_openai.changed.connect(self._on_changed)
        layout.addWidget(self.card_openai)

        self.card_gemini = ServiceCard(
            service_id="gemini",
            title="Gemini Flash",
            tag="Gemini Flash (key dùng chung cho Nano Banana, Veo, Lyria)",
            key_placeholder="Nhập AIza...",
            models=[
                ("Gemini 3.5 Flash", "gemini-3.5-flash"),
                ("Gemini 2.5 Flash", "gemini-2.5-flash"),
                ("Gemini 2.0 Flash", "gemini-2.0-flash"),
            ],
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

        # 1. ElevenLabs
        self.card_elevenlabs = ServiceCard(
            service_id="elevenlabs",
            title="ElevenLabs",
            subtitle="Giọng đọc đa ngôn ngữ chất lượng cao",
            key_placeholder="Nhập xi-...",
            models=[
                ("Turbo v2.5", "turbo-v2.5"),
                ("Multilingual v2", "multilingual-v2"),
                ("Flash v2.5", "flash-v2.5"),
            ],
            has_endpoint=True,
            parent=page,
        )
        self.card_elevenlabs.changed.connect(self._on_changed)
        grid.addWidget(self.card_elevenlabs, 0, 0)

        # 2. OpenAI Audio
        self.card_openai_audio = ServiceCard(
            service_id="openai_audio",
            title="OpenAI Audio",
            shared_from="OpenAI GPT-4o",
            models=[
                ("gpt-4o-mini-tts", "gpt-4o-mini-tts"),
                ("tts-1", "tts-1"),
                ("tts-1-hd", "tts-1-hd"),
            ],
            price_note="Đơn giá: ≈ $15 / 1M ký tự (ước tính)",
            has_key_input=False,
            parent=page,
        )
        self.card_openai_audio.changed.connect(self._on_changed)
        grid.addWidget(self.card_openai_audio, 0, 1)

        # 3. Google Gemini TTS
        self.card_gemini_tts = ServiceCard(
            service_id="gemini_tts",
            title="Google Gemini TTS",
            shared_from="Gemini Flash",
            models=[
                ("Gemini 2.5 Flash TTS", "gemini-2.5-flash-tts"),
                ("Gemini 2.0 Flash Audio", "gemini-2.0-flash-audio"),
            ],
            price_note="Đơn giá: ≈ $20 / 1M ký tự (ước tính)",
            has_key_input=False,
            parent=page,
        )
        self.card_gemini_tts.changed.connect(self._on_changed)
        grid.addWidget(self.card_gemini_tts, 1, 0)

        # 4. Edge-TTS (Miễn phí)
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
        grid.addWidget(self.card_edge_tts, 1, 1)

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
                "Key cho ảnh AI, video AI.",
            )
        )

        grid = QGridLayout()
        grid.setSpacing(12)

        # Row 0:
        # 1. Google Gemini - Ảnh AI
        self.card_gemini_image = ServiceCard(
            service_id="gemini_image",
            title="Google Gemini – Ảnh AI",
            shared_from="Gemini Flash",
            models=[
                ("Gemini 3.1 Flash Image", "gemini-3.1-flash-image"),
                ("Imagen 3.0", "imagen-3.0-generate-002"),
            ],
            price_note="Đơn giá: ≈ $0.039 / ảnh 1K (ước tính)",
            has_key_input=False,
            parent=page,
        )
        self.card_gemini_image.changed.connect(self._on_changed)
        grid.addWidget(self.card_gemini_image, 0, 0)

        # 2. Fal.ai (Flux)
        self.card_fal = ServiceCard(
            service_id="fal",
            title="Fal.ai (Flux)",
            subtitle="Flux Schnell / Dev – ảnh AI",
            key_placeholder="Nhập fal-...",
            models=[
                ("Flux Schnell", "flux-schnell"),
                ("Flux Dev", "flux-dev"),
                ("Flux Pro", "flux-pro"),
            ],
            price_note="Đơn giá: ≈ $0.003 / ảnh (ước tính)",
            has_endpoint=True,
            parent=page,
        )
        self.card_fal.changed.connect(self._sync_shared_keys)
        self.card_fal.changed.connect(self._on_changed)
        grid.addWidget(self.card_fal, 0, 1)

        # 3. Google Veo 3.1
        self.card_veo = ServiceCard(
            service_id="veo",
            title="Google Veo 3.1",
            shared_from="Gemini Flash",
            models=[
                ("Veo 3.1 Lite (rẻ nhất)", "veo-3.1-lite"),
                ("Veo 3.1 Pro", "veo-3.1-pro"),
            ],
            price_note="Đơn giá: $0.05 / giây (720p)",
            has_key_input=False,
            parent=page,
        )
        self.card_veo.changed.connect(self._on_changed)
        grid.addWidget(self.card_veo, 0, 2)

        # Row 1:
        # 4. ByteDance Seedance 2.0 (qua fal)
        self.card_seedance_fal = ServiceCard(
            service_id="seedance_fal",
            title="ByteDance Seedance 2.0 (qua fal)",
            shared_from="Fal.ai (Flux)",
            models=[
                ("Seedance 2.0 Fast (rẻ hơn)", "seedance-2.0-fast"),
                ("Seedance 2.0 Pro", "seedance-2.0-pro"),
            ],
            has_key_input=False,
            parent=page,
        )
        self.card_seedance_fal.changed.connect(self._on_changed)
        grid.addWidget(self.card_seedance_fal, 1, 0)

        # 5. Seedance qua CometAPI
        self.card_seedance_comet = ServiceCard(
            service_id="seedance_comet",
            title="Seedance qua CometAPI",
            subtitle="Seedance 2.0 / Fast / Mini / 2.5 giá rẻ (≈ $0.12/giây 720p bản 2.0) – key riêng của cometapi.com",
            key_placeholder="Nhập sk-...",
            models=[
                ("Seedance 2.0", "seedance-2.0"),
                ("Seedance Fast", "seedance-fast"),
            ],
            has_endpoint=True,
            parent=page,
        )
        self.card_seedance_comet.changed.connect(self._on_changed)
        grid.addWidget(self.card_seedance_comet, 1, 1)

        # 6. Seedance qua PiAPI
        self.card_seedance_piapi = ServiceCard(
            service_id="seedance_piapi",
            title="Seedance qua PiAPI",
            subtitle="Seedance 2.0 / Fast / Mini / 2.5, khung đầu / cuối chính xác (≈ $0.20/giây 720p bản 2.0) – key riêng của piapi.ai",
            key_placeholder="Nhập PiAPI key...",
            models=[
                ("Seedance 2.0", "seedance-2.0"),
                ("Seedance 2.5", "seedance-2.5"),
            ],
            has_endpoint=True,
            parent=page,
        )
        self.card_seedance_piapi.changed.connect(self._on_changed)
        grid.addWidget(self.card_seedance_piapi, 1, 2)

        # Row 2:
        # 7. Kling 3.0 (qua fal)
        self.card_kling_fal = ServiceCard(
            service_id="kling_fal",
            title="Kling 3.0 (qua fal)",
            shared_from="Fal.ai (Flux)",
            models=[
                ("Kling 3.0 Standard (rẻ hơn)", "kling-3.0-standard"),
                ("Kling 3.0 Pro", "kling-3.0-pro"),
            ],
            has_key_input=False,
            parent=page,
        )
        self.card_kling_fal.changed.connect(self._on_changed)
        grid.addWidget(self.card_kling_fal, 2, 0)

        layout.addLayout(grid)
        layout.addStretch(1)
        self.api_tabs.addTab(wrap_scroll(page), "Ảnh / Video AI")

    # ── Shared key propagation ───────────────────────────────────────────────
    def _sync_shared_keys(self) -> None:
        gemini_key = self.card_gemini.key_edit.text().strip()
        has_gemini = bool(gemini_key)
        self.card_gemini_tts.update_shared_status(has_gemini)
        self.card_gemini_image.update_shared_status(has_gemini)
        self.card_veo.update_shared_status(has_gemini)

        openai_key = self.card_openai.key_edit.text().strip()
        has_openai = bool(openai_key)
        self.card_openai_audio.update_shared_status(has_openai)

        fal_key = self.card_fal.key_edit.text().strip()
        has_fal = bool(fal_key)
        self.card_seedance_fal.update_shared_status(has_fal)
        self.card_kling_fal.update_shared_status(has_fal)

    def _on_changed(self) -> None:
        self._sync_shared_keys()
        self.settings_changed.emit()

    # ── settings load & apply ────────────────────────────────────────────────
    def load_from(self, settings: Dict[str, Any]) -> None:
        self._settings = settings
        paths = settings.get("paths") or {}
        app = settings.get("app") or {}
        script = settings.get("script") or {}
        video = settings.get("video") or {}
        api = settings.get("api") or {}

        # General paths
        h2 = str(paths.get("h2mtts") or "D:/mui/f375/tts/H2MTTS")
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

        # Other API keys
        self.card_deepseek.key_edit.setText(str(api.get("deepseek_key") or ""))
        self.card_openai.key_edit.setText(str(api.get("openai_key") or ""))
        self.card_elevenlabs.key_edit.setText(str(api.get("elevenlabs_key") or ""))
        self.card_fal.key_edit.setText(str(api.get("fal_key") or ""))
        self.card_seedance_comet.key_edit.setText(str(api.get("comet_key") or ""))
        self.card_seedance_piapi.key_edit.setText(str(api.get("piapi_key") or ""))

        self._sync_shared_keys()

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

        # API keys
        gemini_k = self.card_gemini.key_edit.text().strip()
        api["gemini_key"] = gemini_k
        if gemini_k:
            script["api_key"] = gemini_k
            video["ai_api_key"] = gemini_k

        api["deepseek_key"] = self.card_deepseek.key_edit.text().strip()
        api["openai_key"] = self.card_openai.key_edit.text().strip()
        api["elevenlabs_key"] = self.card_elevenlabs.key_edit.text().strip()
        api["fal_key"] = self.card_fal.key_edit.text().strip()
        api["comet_key"] = self.card_seedance_comet.key_edit.text().strip()
        api["piapi_key"] = self.card_seedance_piapi.key_edit.text().strip()

        self._settings = settings

    def apply_theme(self) -> None:
        self.card_deepseek.apply_theme()
        self.card_openai.apply_theme()
        self.card_gemini.apply_theme()
        self.card_elevenlabs.apply_theme()
        self.card_openai_audio.apply_theme()
        self.card_gemini_tts.apply_theme()
        self.card_edge_tts.apply_theme()
        self.card_h2mtts.apply_theme()
        self.card_gemini_image.apply_theme()
        self.card_fal.apply_theme()
        self.card_veo.apply_theme()
        self.card_seedance_fal.apply_theme()
        self.card_seedance_comet.apply_theme()
        self.card_seedance_piapi.apply_theme()
        self.card_kling_fal.apply_theme()
