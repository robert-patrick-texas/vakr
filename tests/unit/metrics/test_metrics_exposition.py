"""The text exposition writer: format, escaping, absence of unknowns, and the caps (E.33).

Dependencies:
    pytest; tests.unit.metrics.metrics_testkit.

Example:
    $ uv run pytest tests/unit/metrics/test_metrics_exposition.py
"""

import math

import pytest

from tests.unit.metrics.metrics_testkit import parse
from vakr.metrics.exposition import (
    MAX_SERIES,
    Family,
    Kind,
    Sample,
    escape_label,
    format_value,
    render,
)


def _gauge(name: str, *values: float, label: str = "x") -> Family:
    """Make a gauge with one sample per value, labelled by index.

    Args:
        name: Family name.
        *values: Values.
        label: Label name.

    Returns:
        Family: The gauge.

    Raises:
        None
    """
    samples = tuple(Sample(((label, str(i)),), v) for i, v in enumerate(values))
    return Family(name, Kind.GAUGE, f"{name} help.", samples)


def test_e33_format_escapes_and_parses_strictly() -> None:
    """E.33: HELP and TYPE once per family; label values escaped; the strict parser accepts it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tricky = Family("vakr_t", Kind.COUNTER, 'Back\\slash\nnewline "q".',
                    (Sample((("v", 'a"b\\c\nd'),), 3),))  # fmt: skip
    text = render([tricky, _gauge("vakr_g", 0.25, 1e-6)])
    assert 'vakr_t{v="a\\"b\\\\c\\nd"} 3' in text
    assert "# HELP vakr_t Back\\\\slash\\nnewline" in text
    families = parse(text)
    assert families["vakr_t"].kind == "counter"
    assert list(families["vakr_g"].values().values()) == [0.25, 1e-6]
    assert families["vakr_metrics_truncated"].values() == {(): 0.0}


def test_invariant1_non_finite_values_are_absent_and_empty_families_skipped() -> None:
    """Invariant 1: NaN and infinity are never written; a family without a value has no lines.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = render([_gauge("vakr_a", math.nan, 2), _gauge("vakr_b", math.inf)])
    assert "NaN" not in text
    assert "Inf" not in text
    assert 'vakr_a{x="1"} 2' in text
    assert 'vakr_a{x="0"}' not in text
    assert "vakr_b" not in text
    with pytest.raises(ValueError, match="finite"):
        format_value(math.nan)
    assert (format_value(3.0), format_value(2**60), escape_label("plain")) == (
        "3", "1.152921504606847e+18", "plain")  # fmt: skip


def test_e33_series_and_size_caps_drop_whole_families_and_say_so() -> None:
    """E.33 #7: a family crossing a cap is left out with the rest, and truncated is 1.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    small, big = _gauge("vakr_small", 1), _gauge("vakr_big", *range(MAX_SERIES))
    text = render([small, big, _gauge("vakr_after", 1)])
    families = parse(text)
    assert set(families) == {"vakr_small", "vakr_metrics_truncated"}
    assert families["vakr_metrics_truncated"].values() == {(): 1.0}
    wide = _gauge("vakr_wide", *range(400), label="a_long_label_name_to_fill_the_byte_budget")
    assert parse(render([small, wide], max_bytes=4096))["vakr_metrics_truncated"].values() == {
        (): 1.0}  # fmt: skip


def test_bad_names_are_programming_errors() -> None:
    """E.33: names and label names are checked when a family is made.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="metric name"):
        Family("9bad", Kind.GAUGE, "x", ())
    with pytest.raises(ValueError, match="label name"):
        Sample((("__reserved", "x"),), 1)
    with pytest.raises(ValueError, match="suffix"):
        Sample((), 1, "_bucket")
    with pytest.raises(TypeError, match="string"):
        Sample((("a", 1),), 1)  # ty: ignore[invalid-argument-type]
