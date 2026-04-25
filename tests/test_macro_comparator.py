from __future__ import annotations

from src.agents import macro_comparator
from src.agents.macro_comparator import FIELD_SCALES, ComparatorResult
from src.types import MacroSnapshot


def _full(**kwargs) -> MacroSnapshot:
    base = dict(
        cpi_yoy=2.0,
        core_pce_yoy=2.0,
        fed_funds=2.0,
        ten_year=3.0,
        dxy=100.0,
        unemployment=4.0,
        real_gdp_yoy=2.0,
    )
    base.update(kwargs)
    return MacroSnapshot(**base)


def test_module_has_run():
    assert hasattr(macro_comparator, "run")


def test_identical_snapshots_score_one():
    a = _full()
    b = _full()
    result = macro_comparator.run(a, b)
    assert result.similarity == 1.0
    assert result.diverging_dimensions == []


def test_one_scale_apart_per_field_gives_known_similarity():
    a = _full()
    b = _full(
        cpi_yoy=4.0,          # +1 scale (2.0 pp on a 2.0 scale)
        core_pce_yoy=3.5,     # +1 scale
        fed_funds=4.0,        # +1 scale
        ten_year=4.5,         # +1 scale
        dxy=110.0,            # +1 scale
        unemployment=6.0,     # +1 scale
        real_gdp_yoy=4.0,     # +1 scale
    )
    result = macro_comparator.run(a, b)
    # mean distance = 1.0 across all fields, similarity = exp(-1) ≈ 0.3679
    assert 0.36 < result.similarity < 0.38


def test_diverging_dimensions_top_three_by_distance():
    a = _full()
    b = _full(cpi_yoy=10.0, dxy=200.0, fed_funds=10.0, ten_year=3.1)
    result = macro_comparator.run(a, b)
    assert len(result.diverging_dimensions) == 3
    assert result.diverging_dimensions[0] in {"cpi_yoy", "dxy", "fed_funds"}
    # ten_year only differs by 0.1, should not be in the top 3
    assert "ten_year" not in result.diverging_dimensions


def test_missing_fields_are_skipped_not_penalized():
    a = MacroSnapshot(cpi_yoy=2.0, fed_funds=2.0)
    b = MacroSnapshot(cpi_yoy=2.0, fed_funds=2.0)  # other fields None on both
    result = macro_comparator.run(a, b)
    assert result.similarity == 1.0
    assert set(result.distances.keys()) == {"cpi_yoy", "fed_funds"}


def test_no_overlapping_fields_returns_zero_similarity():
    a = MacroSnapshot(cpi_yoy=2.0)
    b = MacroSnapshot(fed_funds=2.0)
    result = macro_comparator.run(a, b)
    assert result.similarity == 0.0
    assert result.diverging_dimensions == []


def test_field_scales_match_dataclass_fields():
    """If types.py changes MacroSnapshot fields, FIELD_SCALES should track."""
    snap = MacroSnapshot()
    for fname in FIELD_SCALES:
        assert hasattr(snap, fname)


def test_distances_are_normalized_by_scale():
    a = _full()
    b = _full(cpi_yoy=4.0)  # 2pp apart, scale is 2.0, normalized = 1.0
    result = macro_comparator.run(a, b)
    assert result.distances["cpi_yoy"] == 1.0
