"""Pure timing maths used when joining dubbed cues."""
from __future__ import annotations

import pytest

from core.dubbing import fit_plan, natural_timings


def test_fit_plan_without_slot_is_natural() -> None:
    plan = fit_plan(3.0, 0.0)
    assert plan == {"tempo": 1.0, "pad": 0.0, "trim": 0.0, "natural": True}
    assert fit_plan(3.0, -2.0)["natural"] is True


def test_fit_plan_pads_short_audio() -> None:
    plan = fit_plan(2.0, 3.5)
    assert plan["tempo"] == 1.0
    assert plan["pad"] == pytest.approx(1.5)
    assert plan["trim"] == 0.0
    assert plan["natural"] is False


def test_fit_plan_speeds_up_within_cap() -> None:
    plan = fit_plan(2.6, 2.0, max_tempo=1.3)
    assert plan["tempo"] == pytest.approx(1.3)
    assert plan["pad"] == 0.0
    assert plan["trim"] == 0.0

    plan = fit_plan(2.2, 2.0, max_tempo=1.3)
    assert plan["tempo"] == pytest.approx(1.1)


def test_fit_plan_trims_when_cap_is_not_enough() -> None:
    plan = fit_plan(5.0, 2.0, max_tempo=1.3)
    assert plan["tempo"] == pytest.approx(1.3)
    assert plan["pad"] == 0.0
    assert plan["trim"] == pytest.approx(5.0 / 1.3 - 2.0)


def test_fit_plan_handles_negative_values() -> None:
    plan = fit_plan(-1.0, 1.0)
    assert plan["tempo"] == 1.0 and plan["pad"] == pytest.approx(1.0)


def test_natural_timings_accumulate_with_gap() -> None:
    timings = natural_timings([1.0, 2.0, 0.5], gap=0.2)
    assert timings[0] == (0.0, 1.0)
    assert timings[1] == (pytest.approx(1.2), pytest.approx(3.2))
    assert timings[2] == (pytest.approx(3.4), pytest.approx(3.9))


def test_natural_timings_start_offset_and_negative_gap() -> None:
    timings = natural_timings([1.0], gap=-5.0, start=2.0)
    assert timings == [(2.0, 3.0)]
