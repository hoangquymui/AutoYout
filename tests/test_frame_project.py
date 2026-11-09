import json
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QLabel
from core.frame_layout import FrameImage, FrameLayout
from core.project_manager import ProjectManager
from core.ffmpeg_renderer import VideoConfig
from ui.tab_frame import FrameTab


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_frame_tab_renamed_to_anh(app):
    tab = FrameTab()
    texts = [btn.text() for btn in tab.findChildren(QPushButton)]
    assert "Thêm ảnh từ file..." in texts or any("Thêm ảnh" in t for t in texts)
    labels = [lbl.text() for lbl in tab.findChildren(QLabel)]
    assert any("Chèn nhanh ảnh" in l for l in labels)
    assert not any("Chèn nhanh sticker" in l for l in labels)


def test_frame_auto_saved_to_project_and_images_copied(tmp_path: Path, app):
    proj_dir = tmp_path / "TestProject"
    proj_dir.mkdir()
    manager = ProjectManager(proj_dir)

    # Create an external image file
    external_img = tmp_path / "banner_outside.png"
    external_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake")

    tab = FrameTab()
    tab.set_project(manager)

    # Change ratio and add image
    tab._layout.enabled = True
    tab._layout.aspect_ratio = "9:16"
    tab._layout.images.append(FrameImage(path=str(external_img), x=0.2, y=0.2, width=0.3))
    tab._commit_history()

    # Verify video/frame folder exists
    frame_dir = proj_dir / "video" / "frame"
    assert frame_dir.is_dir()

    # Verify external image was copied into video/frame
    copied_img = frame_dir / "banner_outside.png"
    assert copied_img.is_file()
    assert copied_img.read_bytes() == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake"

    # Verify frame.json exists and contains correct settings
    frame_json = frame_dir / "frame.json"
    assert frame_json.is_file()
    data = json.loads(frame_json.read_text(encoding="utf-8"))
    assert data.get("aspect_ratio") == "9:16"
    assert data.get("enabled") is True
    assert len(data.get("images", [])) == 1

    # Verify VideoConfig.from_settings loads project frame
    cfg = VideoConfig.from_settings({}, manager=manager)
    assert cfg.frame.aspect_ratio == "9:16"
    assert cfg.frame.enabled is True
    assert len(cfg.frame.images) == 1
    assert Path(cfg.frame.images[0].path).resolve() == copied_img.resolve()

    # Now open a new FrameTab and set project, verify it loads the saved frame
    new_tab = FrameTab()
    new_tab.set_project(manager)
    assert new_tab._layout.aspect_ratio == "9:16"
    assert new_tab._layout.enabled is True
    assert len(new_tab._layout.images) == 1
    assert Path(new_tab._layout.images[0].path).resolve() == copied_img.resolve()


def test_frame_tab_delete_on_canvas_updates_layer_list(app):
    tab = FrameTab()
    tab._layout.images.append(FrameImage(path="dummy.png", x=0.1, y=0.1, width=0.2))
    tab._rebuild_list(0)
    assert any("dummy.png" in tab._image_list.item(r).text() for r in range(tab._image_list.count()))

    # Select on canvas and delete
    tab._canvas.set_selection("image", 0)
    assert tab._canvas.delete_selected() is True

    # Layer list must be updated immediately
    assert not any("dummy.png" in tab._image_list.item(r).text() for r in range(tab._image_list.count()))


def test_save_button_in_toolbar(tmp_path: Path, app):
    proj_dir = tmp_path / "MyProject"
    proj_dir.mkdir()
    manager = ProjectManager(proj_dir)

    tab = FrameTab()
    assert hasattr(tab, "_save_btn")
    assert tab._save_btn.text() == "Lưu"

    tab.set_project(manager)
    tab._layout.enabled = True
    tab._layout.aspect_ratio = "1:1"
    tab._save_btn.click()

    frame_json = proj_dir / "video" / "frame" / "frame.json"
    assert frame_json.is_file()
    data = json.loads(frame_json.read_text(encoding="utf-8"))
    assert data.get("aspect_ratio") == "1:1"
    assert "Đã lưu thành công" in tab._status.text()
