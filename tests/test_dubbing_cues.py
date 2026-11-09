"""Cue model, import and split/merge behaviour of the dubbing module."""
from __future__ import annotations

from pathlib import Path

from core.dubbing import (
    STATUS_PENDING,
    CueJob,
    apply_import_options,
    import_srt,
    import_txt,
    load_cues,
    merge_short_cues,
    reindex,
    split_cues_by_words,
)
from core.project_manager import ProjectManager


def _cue(cue_id: int, start: float, end: float, text: str, **kwargs) -> CueJob:
    return CueJob(cue_id, start, end, text, **kwargs)


def test_cue_job_state_roundtrip() -> None:
    cue = _cue(3, 1.25, 4.5, "Xin chào", voice="Hải Đăng")
    cue.status = "DONE"
    cue.out = "audio/cues/tap/0003.mp3"
    cue.text_hash = "abc"
    restored = CueJob.from_state(cue.to_state(), 1)

    assert restored.id == 3
    assert restored.start == 1.25
    assert restored.end == 4.5
    assert restored.text == "Xin chào"
    assert restored.voice == "Hải Đăng"
    assert restored.status == "DONE"
    assert restored.out == "audio/cues/tap/0003.mp3"
    assert restored.text_hash == "abc"


def test_cue_job_swaps_reversed_timing() -> None:
    cue = CueJob.from_state({"start": 5, "end": 2, "text": "x"}, 1)
    assert cue.start == 2.0 and cue.end == 5.0
    assert cue.duration == 3.0


def test_split_cues_by_words_keeps_total_duration() -> None:
    text = ("Đây là một câu rất dài cần được chia nhỏ để tổng hợp giọng đọc ổn định. " * 6).strip()
    cue = _cue(1, 10.0, 46.0, text)

    pieces = split_cues_by_words([cue], min_words=3, max_words=6)

    assert len(pieces) > 1
    assert all(len(piece.text.split()) <= 6 for piece in pieces)
    assert pieces[0].start == 10.0
    assert pieces[-1].end == 46.0
    for previous, following in zip(pieces, pieces[1:]):
        assert abs(previous.end - following.start) < 1e-6
    assert [piece.id for piece in pieces] == list(range(1, len(pieces) + 1))


def test_split_cues_by_words_resets_status_and_voice() -> None:
    cue = _cue(1, 0.0, 10.0, ("Aaa " * 100).strip())
    cue.voice = "Nam"
    cue.status = "DONE"

    pieces = split_cues_by_words([cue], min_words=3, max_words=10)

    assert len(pieces) > 1
    assert all(len(piece.text.split()) <= 10 for piece in pieces)
    assert all(piece.voice == "Nam" for piece in pieces)
    assert all(piece.status == STATUS_PENDING for piece in pieces)


def test_merge_short_cues_respects_gap() -> None:
    cues = [
        _cue(1, 0.0, 1.0, "Ngắn"),
        _cue(2, 1.2, 4.0, "Câu thứ hai dài hơn một chút để kiểm tra việc gộp."),
        _cue(3, 8.0, 9.0, "Đứng riêng một mình ở đây."),
        _cue(4, 9.1, 12.0, "Câu cuối cùng cũng khá dài để không bị gộp."),
    ]

    merged = merge_short_cues(cues, min_chars=25, max_gap=0.4)

    assert len(merged) == 3
    assert merged[0].text.startswith("Ngắn Câu thứ hai")
    assert merged[0].end == 4.0
    assert merged[1].text == "Đứng riêng một mình ở đây."
    assert [cue.id for cue in merged] == [1, 2, 3]


def test_apply_import_options_reindexes() -> None:
    cues = [
        _cue(9, 0.0, 1.0, "Một"),
        _cue(9, 1.1, 5.0, "Hai " * 100),
    ]
    result = apply_import_options(cues, auto_split=True, min_words=3,
                                  max_words=6, merge_short=False)
    assert [cue.id for cue in result] == list(range(1, len(result) + 1))


def test_import_srt_creates_input_text(tmp_path: Path) -> None:
    srt = tmp_path / "Bài giảng 01.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nXin chào các bạn.\n\n"
        "2\n00:00:04,000 --> 00:00:06,500\nHôm nay chúng ta học bài mới.\n",
        encoding="utf-8",
    )
    manager = ProjectManager(tmp_path / "project")

    base = import_srt(manager, srt)

    assert base == "Bài giảng 01"
    txt = manager.script_dir / f"{base}.txt"
    assert txt.is_file()
    assert "Xin chào các bạn." in txt.read_text(encoding="utf-8")
    cues = load_cues(manager, base)
    assert len(cues) == 2
    assert cues[1].start == 4.0
    manager.scan()
    item = manager.get_item(base)
    assert item is not None and item.dub_mode == "srt"
    assert Path(item.cues_path).name == f"{base}.cues.json"


def test_reindex_renumbers() -> None:
    cues = [_cue(5, 0, 1, "a"), _cue(8, 1, 2, "b")]
    assert [cue.id for cue in reindex(cues)] == [1, 2]


def test_import_txt_splits_by_word_range(tmp_path: Path) -> None:
    txt = tmp_path / "Câu chuyện.txt"
    txt.write_text(
        ("Câu một. Câu hai. Câu ba. Câu bốn. " * 12).strip(),
        encoding="utf-8",
    )
    manager = ProjectManager(tmp_path / "project")

    base = import_txt(manager, txt, min_words=3, max_words=6)

    assert base == "Câu chuyện"
    cues = load_cues(manager, base)
    assert len(cues) > 1
    assert all(len(cue.text.split()) <= 6 for cue in cues)
    assert cues[0].start == 0.0
    for previous, following in zip(cues, cues[1:]):
        assert abs(previous.end - following.start) < 1e-6
    manager.scan()
    item = manager.get_item(base)
    assert item is not None and item.dub_mode == "srt"


def test_split_cues_by_sentences_and_import_mode(tmp_path: Path) -> None:
    from core.dubbing import (
        apply_import_options,
        import_txt,
        load_cues,
        split_cues_by_sentences,
    )
    pm = ProjectManager(tmp_path)

    # 1. split_cues_by_sentences
    cue = _cue(1, 10.0, 25.0, "Câu thứ nhất đã xong. Câu thứ hai tiếp diễn! Câu thứ ba kết thúc.")
    cues = split_cues_by_sentences([cue])
    assert len(cues) == 3
    assert cues[0].text == "Câu thứ nhất đã xong."
    assert cues[1].text == "Câu thứ hai tiếp diễn!"
    assert cues[2].text == "Câu thứ ba kết thúc."
    assert cues[0].start == 10.0
    assert abs(cues[-1].end - 25.0) < 1e-4

    # 2. import_txt with split_mode="sentence"
    txt_file = tmp_path / "sample_story.txt"
    txt_file.write_text(
        "Ngày xửa ngày xưa có một chú mèo con. Chú rất thích ăn cá! Nhưng hôm nay trời mưa to.",
        encoding="utf-8",
    )
    base = import_txt(pm, txt_file, split_mode="sentence")
    imported_cues = load_cues(pm, base)
    assert len(imported_cues) == 3
    assert imported_cues[0].text == "Ngày xửa ngày xưa có một chú mèo con."
    assert imported_cues[1].text == "Chú rất thích ăn cá!"
    assert imported_cues[2].text == "Nhưng hôm nay trời mưa to."

    # 3. apply_import_options with split_mode="sentence"
    raw_cue = _cue(1, 0.0, 10.0, "Đoạn A. Đoạn B! Đoạn C?")
    options_res = apply_import_options([raw_cue], auto_split=True, split_mode="sentence")
    assert len(options_res) == 3
    assert [c.text for c in options_res] == ["Đoạn A.", "Đoạn B!", "Đoạn C?"]

