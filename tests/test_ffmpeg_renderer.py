"""Renderer job planning and a real short encode."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import requires_ffmpeg
from core.ffmpeg_renderer import FFmpegRenderer, RenderError, VideoConfig
from core.frame_layout import FrameImage, FrameLayout
from core.project_manager import ProjectItem, ProjectManager, VIDEO_DONE
from core.settings import load_settings


def _manager(tmp_path: Path, count: int = 3) -> ProjectManager:
    inputs = tmp_path / "script"
    inputs.mkdir(parents=True, exist_ok=True)
    for index in range(1, count + 1):
        (inputs / f"tap_{index}.txt").write_text(f"Nội dung phần {index}.", encoding="utf-8")
    manager = ProjectManager(tmp_path)
    manager.scan()
    return manager


def _config(tmp_path: Path, *, frame=None, **overrides) -> VideoConfig:
    raw = {
        "grouping": "one",
        "group_size": 2,
        "visual": "image",
        "image": str(tmp_path / "bg.png"),
        "resolution": "640x360",
        "fps": 24,
        "encoder": "libx264",
        "crf": 30,
        "gap": 0.2,
    }
    raw.update(overrides)
    settings = {"video": raw}
    if frame is not None:
        settings["frame"] = frame
    return VideoConfig.from_settings(settings)


def _renderer() -> FFmpegRenderer:
    settings = load_settings()
    return FFmpegRenderer(settings)


def test_plans_one_video_per_item(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 3)
    jobs = _renderer().build_jobs(manager, _config(tmp_path))
    assert [job.id for job in jobs] == ["one::tap_1", "one::tap_2", "one::tap_3"]
    assert all(len(job.items) == 1 for job in jobs)
    assert all(job.path.parent == manager.video_dir for job in jobs)


def test_plans_groups_of_n(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 5)
    jobs = _renderer().build_jobs(manager, _config(tmp_path, grouping="group", group_size=2))
    assert [job.id for job in jobs] == ["group::01", "group::02", "group::03"]
    assert [len(job.items) for job in jobs] == [2, 2, 1]
    assert jobs[0].path.name.endswith("_part_01.mp4")


def test_plans_all_in_one(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 3)
    jobs = _renderer().build_jobs(manager, _config(tmp_path, grouping="all"))
    assert len(jobs) == 1
    assert jobs[0].id == "all"
    assert len(jobs[0].items) == 3


def test_invalid_grouping_raises(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 1)
    cfg = VideoConfig.from_settings({"video": {"grouping": "nope"}})
    with pytest.raises(RenderError):
        _renderer().build_jobs(manager, cfg)


def _make_media(tmp_path: Path, ffmpeg: str) -> Path:
    image = tmp_path / "bg.png"
    audio = tmp_path / "audio" / "tap_1.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x08000000
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=0x1b2740:s=640x360:d=1", "-frames:v", "1", str(image)],
        check=True, creationflags=flags,
    )
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame",
         "-b:a", "192k", str(audio)],
        check=True, creationflags=flags,
    )
    return image


@requires_ffmpeg
def test_render_short_video(tmp_path: Path, ffmpeg: str) -> None:
    _manager(tmp_path, 1)
    image = _make_media(tmp_path, ffmpeg)
    manager = ProjectManager(tmp_path)
    manager.scan()
    renderer = _renderer()
    cfg = _config(tmp_path, image=str(image))

    progress: list[int] = []
    jobs, errors = renderer.render_all(
        manager, cfg, force=True,
        progress_cb=lambda value, _message: progress.append(value),
    )

    assert errors == []
    assert len(jobs) == 1
    job = manager.get_video("one::tap_1")
    assert job is not None and job.state == VIDEO_DONE
    assert job.path.is_file() and job.path.stat().st_size > 2000
    assert job.timestamps_text.startswith("0:00")
    assert max(progress) >= 90
    assert renderer.needs_render(manager, job) is False
    assert renderer.needs_render(manager, job, force=True) is True


def test_ai_visual_config_validates(tmp_path: Path) -> None:
    cfg = _config(tmp_path, visual="ai", ai_mode="manual", ai_count=3)
    assert cfg.errors() == []
    bad_mode = _config(tmp_path, visual="ai", ai_mode="dalle")
    assert any("Chế độ ảnh AI" in problem for problem in bad_mode.errors())
    too_many = VideoConfig(visual="ai", ai_count=99)
    assert any("Số ảnh AI" in problem for problem in too_many.errors())


def _make_chapter_images(folder: Path, ffmpeg: str, base: str, count: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    colors = ["0x1b2740", "0x3a2f5b", "0x2e4a3f", "0x5b3a2f"]
    for index in range(count):
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"color=c={colors[index % len(colors)]}:s=640x360:d=1",
             "-frames:v", "1", str(folder / f"{base}_{index + 1:02d}.png")],
            check=True, creationflags=0x08000000,
        )


@requires_ffmpeg
def test_render_ai_chapters_with_motion_and_xfade(tmp_path: Path, ffmpeg: str) -> None:
    _manager(tmp_path, 2)
    _make_media(tmp_path, ffmpeg)
    audio_dir = tmp_path / "audio"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=330:duration=2", "-c:a", "libmp3lame",
         "-b:a", "192k", str(audio_dir / "tap_2.mp3")],
        check=True, creationflags=0x08000000,
    )
    manager = ProjectManager(tmp_path)
    manager.scan()
    ai_dir = manager.assets_dir / "ai"
    _make_chapter_images(ai_dir, ffmpeg, "tap_1", 2)
    _make_chapter_images(ai_dir, ffmpeg, "tap_2", 1)

    cfg = _config(
        tmp_path, grouping="all", visual="ai", ai_mode="manual",
        ai_dir=str(ai_dir), ai_count=2, ai_motion=True, ai_xfade=0.3,
        resolution="320x180", fps=12, crf=40, chapter_titles=False,
    )
    assert cfg.errors() == []
    renderer = _renderer()
    jobs, errors = renderer.render_all(manager, cfg, force=True)

    assert errors == []
    assert len(jobs) == 1
    job = manager.get_video("all")
    assert job is not None and job.state == VIDEO_DONE
    assert job.path.is_file() and job.path.stat().st_size > 2000
    assert 3.5 < job.duration < 5.5


@requires_ffmpeg
def test_render_ai_manual_mode_requires_images(tmp_path: Path, ffmpeg: str) -> None:
    _manager(tmp_path, 1)
    _make_media(tmp_path, ffmpeg)
    manager = ProjectManager(tmp_path)
    manager.scan()
    ai_dir = manager.assets_dir / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    cfg = _config(
        tmp_path, visual="ai", ai_mode="manual", ai_dir=str(ai_dir),
        resolution="320x180", fps=12, crf=40,
    )
    jobs, errors = _renderer().render_all(manager, cfg, force=True)
    assert jobs == []
    assert len(errors) == 1
    assert "Thiếu ảnh cho chương" in errors[0]


def test_ai_imagen_without_key_is_rejected(tmp_path: Path) -> None:
    _manager(tmp_path, 1)
    manager = ProjectManager(tmp_path)
    manager.scan()
    cfg = _config(tmp_path, visual="ai", ai_mode="imagen", ai_api_key="")
    with pytest.raises(RenderError):
        _renderer()._validate_inputs(manager, manager.get_items(), cfg)


# ── phụ đề (subtitles) + duck nhạc nền ────────────────────────────────────────
def _item(tmp_path: Path, text: str = "",
          metadata: dict | None = None) -> ProjectItem:
    return ProjectItem(
        base="tap_1", text_file=None, json_file=None, text=text,
        text_hash="", metadata=metadata or {},
        audio_path=tmp_path / "audio" / "tap_1.mp3",
    )


def test_config_reads_subtitle_and_duck_settings() -> None:
    cfg = VideoConfig.from_settings({
        "video": {"subtitle_enabled": True, "bgm_duck": 0.4},
    })
    assert cfg.subtitle_enabled is True
    assert cfg.bgm_duck == 0.4
    defaults = VideoConfig.from_settings({})
    assert defaults.subtitle_enabled is False
    assert defaults.bgm_duck == 0.25
    bad = VideoConfig(bgm_duck=1.5)
    assert any("bgm_duck" in problem for problem in bad.errors())


def test_duck_expression_during_narration(tmp_path: Path) -> None:
    item = _item(tmp_path)
    segments = [(item, 1.0, 4.0), (item, 6.5, 2.0)]
    expr = FFmpegRenderer._duck_expression(segments, 0.25)
    assert expr.startswith("if(")
    assert "between(t,1.000,5.000)" in expr
    assert "between(t,6.500,8.500)" in expr
    assert expr.endswith(",0.2500,1)")
    # no ducking when level >= 1 or no segments
    assert FFmpegRenderer._duck_expression(segments, 1.0) == ""
    assert FFmpegRenderer._duck_expression([], 0.25) == ""
    assert FFmpegRenderer._duck_expression(segments, 0.0).endswith(",0.0000,1)")


def test_split_subtitle_text_events() -> None:
    text = "Câu một. Câu hai! Câu ba?"
    events = FFmpegRenderer._split_subtitle_text(text, 2.0, 8.0)
    assert len(events) == 3
    assert events[0][0] == 2.0
    assert events[-1][1] == 8.0
    assert " ".join(chunk for _, _, chunk in events) == text
    assert all(start < end for start, end, _ in events)
    # long text in a short slot merges into fewer, readable events
    many = ". ".join(f"Câu thứ {i}" for i in range(1, 7)) + "."
    merged = FFmpegRenderer._split_subtitle_text(many, 0.0, 2.4)
    assert 1 <= len(merged) <= 2
    assert FFmpegRenderer._split_subtitle_text("", 0.0, 5.0) == []


def test_subtitle_text_from_item_and_metadata_chapters(tmp_path: Path) -> None:
    item = _item(tmp_path, text="Nội dung thuyết minh. Gồm hai câu.")
    segments = [(item, 0.0, 4.0)]
    events = FFmpegRenderer._subtitle_events(segments, 4.0)
    assert len(events) == 2
    assert all(0.0 <= start < end <= 4.0 for start, end, _ in events)
    meta_item = _item(tmp_path, text="", metadata={
        "chapters": [{"title": "Mở đầu"}, {"title": "Kết"}],
    })
    assert FFmpegRenderer._item_subtitle_text(meta_item) == "Mở đầu\nKết"


def test_write_subtitles_creates_ass_with_vietnamese(tmp_path: Path) -> None:
    item = _item(tmp_path, text="Chào bạn! Đây là phụ đề tiếng Việt.")
    cfg = _config(tmp_path, subtitle_enabled=True)
    renderer = _renderer()
    assert renderer._write_subtitles([(item, 0.0, 3.0)], 3.0, tmp_path, cfg) is True
    ass = (tmp_path / "subs.ass").read_text(encoding="utf-8")
    assert "[V4+ Styles]" in ass
    assert "Style: Sub,Arial," in ass
    assert "Dialogue:" in ass
    assert "phụ đề tiếng Việt" in ass
    # no text → no file
    assert renderer._write_subtitles([(_item(tmp_path), 0.0, 3.0)], 3.0, tmp_path, cfg) is False


def test_build_filter_burns_subtitles_and_ducks_bgm(tmp_path: Path) -> None:
    item = _item(tmp_path, text="Chào bạn! Phụ đề tiếng Việt.")
    segments = [(item, 0.0, 3.0)]
    cfg = _config(tmp_path, subtitle_enabled=True, bgm_duck=0.3)
    graph, vlabel, _alabel = _renderer()._build_filter(
        cfg, {"kind": "image", "path": str(tmp_path / "bg.png")},
        [], 3.0, tmp_path, has_bgm=True, segments=segments)
    assert "subtitles=subs.ass:fontsdir=." in graph
    assert f"[{vlabel}]null[vout]" in graph
    assert "volume='if(between(t,0.000,3.000),0.3000,1)':eval=frame" in graph


@requires_ffmpeg
def test_render_with_subtitles_and_bgm_duck(tmp_path: Path, ffmpeg: str) -> None:
    _manager(tmp_path, 1)
    image = _make_media(tmp_path, ffmpeg)
    bgm = tmp_path / "bgm.mp3"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=220:duration=4", "-c:a", "libmp3lame",
         "-b:a", "128k", str(bgm)],
        check=True, creationflags=0x08000000,
    )
    manager = ProjectManager(tmp_path)
    manager.scan()
    item = manager.get_items()[0]
    item.text = "Chào bạn. Đây là phụ đề tiếng Việt!"
    renderer = _renderer()
    if not renderer.has_filter("subtitles"):
        pytest.skip("ffmpeg thiếu filter subtitles (libass)")
    cfg = _config(
        tmp_path, image=str(image), subtitle_enabled=True,
        bgm=str(bgm), bgm_duck=0.25,
    )
    jobs, errors = renderer.render_all(manager, cfg, force=True)
    assert errors == []
    assert len(jobs) == 1
    job = manager.get_video("one::tap_1")
    assert job is not None and job.state == VIDEO_DONE
    assert job.path.is_file() and job.path.stat().st_size > 2000


# ── gắn file .srt có sẵn ──────────────────────────────────────────────────────
def test_config_reads_srt_subtitle_settings() -> None:
    cfg = VideoConfig.from_settings({
        "video": {"subtitle_srt": True, "subtitle_file": "D:/subs/tap_1.srt"},
    })
    assert cfg.subtitle_srt is True
    assert cfg.subtitle_file == "D:/subs/tap_1.srt"
    defaults = VideoConfig.from_settings({})
    assert defaults.subtitle_srt is False
    assert defaults.subtitle_file == ""


def test_srt_sources_find_sidecars_and_shift_events(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 2)
    items = manager.get_items()
    audio_srt = items[0].audio_path.with_suffix(".srt")
    audio_srt.parent.mkdir(parents=True, exist_ok=True)
    audio_srt.write_text(
        "1\n00:00:00,500 --> 00:00:02,000\nXin chào\n", encoding="utf-8")
    text_srt = items[1].text_file.with_suffix(".srt")
    text_srt.write_text(
        "1\n00:00:00,100 --> 00:00:01,500\nPhần hai\n", encoding="utf-8")

    cfg = _config(tmp_path, subtitle_srt=True)
    renderer = _renderer()
    sources = renderer._srt_sources(manager, items, cfg)
    assert [path.name for _item, path in sources] == ["tap_1.srt", "tap_2.srt"]

    segments = [(items[0], 0.0, 8.0), (items[1], 8.0, 5.0)]
    events = renderer._srt_events(manager, items, cfg, segments)
    assert events[0] == (0.5, 2.0, "Xin chào")
    assert events[1] == (8.1, 9.5, "Phần hai")


def test_missing_explicit_srt_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 1)
    cfg = _config(tmp_path, subtitle_srt=True,
                  subtitle_file=str(tmp_path / "khong_co.srt"))
    with pytest.raises(RenderError):
        _renderer()._srt_sources(manager, manager.get_items(), cfg)


def test_build_filter_burns_existing_srt_events(tmp_path: Path) -> None:
    item = _item(tmp_path)
    cfg = _config(tmp_path, subtitle_srt=True)
    events = [(0.2, 1.8, "Phụ đề lấy từ file SRT")]
    graph, vlabel, _alabel = _renderer()._build_filter(
        cfg, {"kind": "image", "path": str(tmp_path / "bg.png")},
        [], 3.0, tmp_path, has_bgm=False,
        segments=[(item, 0.0, 3.0)], srt_events=events)
    assert "subtitles=subs.ass:fontsdir=." in graph
    assert f"[{vlabel}]null[vout]" in graph
    ass = (tmp_path / "subs.ass").read_text(encoding="utf-8")
    assert "Phụ đề lấy từ file SRT" in ass
    assert "Style: Sub,Arial," in ass


def test_run_encode_caps_length_and_loops_background(tmp_path: Path,
                                                     monkeypatch) -> None:
    cfg = _config(tmp_path)
    renderer = _renderer()
    monkeypatch.setattr(
        renderer, "_build_filter",
        lambda *args, **kwargs: ("[0:a]anull[aout]", "vbase", "aout"))
    captured: dict = {}
    monkeypatch.setattr(
        renderer, "_run_process",
        lambda cmd, *args, **kwargs: captured.update(cmd=cmd))

    def run(visual: dict) -> list:
        captured.clear()
        renderer._run_encode(
            cfg, tmp_path, tmp_path / "combined.wav", visual, [], 7.5,
            tmp_path / "out.mp4", "libx264", None,
            lambda *args: None, None, None)
        return captured["cmd"]

    image_cmd = run({"kind": "image", "path": str(tmp_path / "bg.png")})
    assert image_cmd[image_cmd.index("-t") + 1] == "7.500"
    assert image_cmd[image_cmd.index("-loop") + 1] == "1"

    video_cmd = run({"kind": "video", "path": str(tmp_path / "bg.mp4")})
    assert video_cmd[video_cmd.index("-stream_loop") + 1] == "-1"


def test_config_reads_frame_settings(tmp_path: Path) -> None:
    cfg = VideoConfig.from_settings({
        "video": {},
        "frame": {"enabled": True, "subtitle_y": 0.7, "waveform": True,
                  "wave_color": "ff0000",
                  "images": [{"path": "logo.png", "opacity": 0.4}]},
    })
    assert cfg.frame.enabled is True
    assert cfg.frame.subtitle_y == 0.7
    assert cfg.frame.wave_active is True
    assert cfg.frame.wave_color == "#FF0000"
    assert cfg.frame.images[0].opacity == 0.4


def test_write_ass_uses_frame_position(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.frame = FrameLayout(enabled=True, subtitle_x=0.25, subtitle_y=0.75,
                            subtitle_scale=1.5)
    renderer = _renderer()
    assert renderer._write_ass([(0.5, 2.0, "Xin chào")], 3.0, tmp_path, cfg)
    ass = (tmp_path / "subs.ass").read_text(encoding="utf-8")
    assert "\\pos(160,270)" in ass
    style = next(line for line in ass.splitlines() if line.startswith("Style: Sub,"))
    assert style.endswith(",5,20,20,0,1")


def test_build_filter_places_frame_waveform_and_overlay(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.frame = FrameLayout(
        enabled=True, waveform=True, wave_x=0.1, wave_y=0.8,
        wave_w=0.5, wave_h=0.1, wave_color="#FF0000",
        images=[FrameImage(path="logo.png", x=0.5, y=0.25, width=0.2,
                           opacity=0.5)],
    )
    image = cfg.frame.images[0]
    graph, vlabel, _audio = _renderer()._build_filter(
        cfg, {"kind": "image", "path": str(tmp_path / "bg.png")},
        [], 5.0, tmp_path, has_bgm=True, segments=[],
        frame_images=[(2, image)], bgm_index=3)
    assert "colors=0xFF0000" in graph
    assert "showwaves=s=320x36" in graph
    assert "overlay=64:288" in graph
    assert "[2:v]scale=128:-1,format=rgba,colorchannelmixer=aa=0.500[frameimg0]" in graph
    assert "overlay=320:90:format=auto[vframe0]" in graph
    assert "[3:a]volume=-18.0dB" in graph
    assert vlabel == "vframe0"
    assert "[vframe0]null[vout]" in graph


def test_missing_frame_overlay_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 1)
    cfg = _config(tmp_path)
    assert _renderer()._frame_images(manager, cfg) == []
    cfg.frame = FrameLayout(enabled=True,
                            images=[FrameImage(path="missing.png")])
    with pytest.raises(RenderError):
        _renderer()._frame_images(manager, cfg)


def test_run_encode_adds_overlay_inputs_before_bgm(tmp_path: Path,
                                                   monkeypatch) -> None:
    cfg = _config(tmp_path)
    image = FrameImage(path=str(tmp_path / "logo.png"), x=0.5)
    cfg.frame = FrameLayout(enabled=True, images=[image])
    renderer = _renderer()
    captured: dict = {}
    monkeypatch.setattr(
        renderer, "_build_filter",
        lambda *args, **kwargs: captured.update(filter_kwargs=kwargs)
        or ("[0:a]anull[aout]", "vbase", "aout"))
    monkeypatch.setattr(
        renderer, "_run_process",
        lambda cmd, *args, **kwargs: captured.update(cmd=cmd))

    logo = tmp_path / "logo.png"
    bgm = tmp_path / "bgm.mp3"
    renderer._run_encode(
        cfg, tmp_path, tmp_path / "combined.wav",
        {"kind": "image", "path": str(tmp_path / "bg.png")}, [], 7.5,
        tmp_path / "out.mp4", "libx264", bgm,
        lambda *args: None, None, None,
        frame_images=[(image, logo)])
    cmd = captured["cmd"]
    inputs = [cmd[index + 1] for index, part in enumerate(cmd) if part == "-i"]
    assert inputs[0] == str(tmp_path / "combined.wav")
    assert inputs.index(str(logo)) < inputs.index(str(bgm))
    assert captured["filter_kwargs"]["bgm_index"] == 3
    assert captured["filter_kwargs"]["frame_images"] == [(2, image)]




@requires_ffmpeg
def test_render_with_frame_template(tmp_path: Path, ffmpeg: str) -> None:
    _manager(tmp_path, 1)
    image = _make_media(tmp_path, ffmpeg)
    overlay = tmp_path / "logo.png"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=0xFF0000:s=64x64:d=1", "-frames:v", "1", str(overlay)],
        check=True,
    )
    manager = ProjectManager(tmp_path)
    manager.scan()
    cfg = _config(
        tmp_path,
        image=str(image),
        subtitle_enabled=True,
        frame={
            "enabled": True,
            "subtitle_y": 0.8,
            "subtitle_scale": 1.2,
            "waveform": True,
            "wave_x": 0.1,
            "wave_y": 0.6,
            "wave_w": 0.8,
            "wave_h": 0.1,
            "wave_color": "#FF0000",
            "images": [{
                "path": str(overlay), "x": 0.1, "y": 0.1,
                "width": 0.2, "opacity": 0.8,
            }],
        },
    )

    jobs, errors = _renderer().render_all(manager, cfg, force=True)

    assert errors == []
    assert len(jobs) == 1
    job = manager.get_video("one::tap_1")
    assert job is not None and job.state == VIDEO_DONE
    assert job.path.is_file() and job.path.stat().st_size > 3000


def test_has_audio_and_skip_missing_audio(tmp_path: Path) -> None:
    manager = _manager(tmp_path, 3)
    renderer = _renderer()
    cfg = _config(tmp_path)
    jobs = renderer.build_jobs(manager, cfg)
    
    # Initially none have audio
    for job in jobs:
        assert renderer.has_audio(manager, job) is False
        
    # Create audio for only the first item
    audio_dir = manager.audio_dir
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "tap_1.mp3").write_bytes(b"audio data")
    manager.scan()
    
    assert renderer.has_audio(manager, jobs[0]) is True
    assert renderer.has_audio(manager, jobs[1]) is False
    assert renderer.has_audio(manager, jobs[2]) is False

    # render_all with dummy audio should skip tap_2 and tap_3 without reporting errors for them
    # Note: tap_1 would fail inside ffmpeg encode because b"audio data" is invalid, but tap_2 and tap_3 are skipped before ffmpeg!
    done, errors = renderer.render_all(manager, cfg, skip_missing_audio=True)
    # Check that neither tap_2 nor tap_3 are reported as "Chưa có audio"
    for err in errors:
        assert "tap_2" not in err
        assert "tap_3" not in err
