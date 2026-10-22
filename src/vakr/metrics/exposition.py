"""The Prometheus text exposition format 0.0.4, written by hand (OBS-005, E.33).

A scrape answers families of samples: each family gets one ``# HELP`` and
one ``# TYPE`` line, then one line per sample, ``name{labels} value``.
Summaries carry ``quantile`` samples plus ``_sum`` and ``_count``. vakr has
no runtime dependency for this: the format is small, and writing it here
keeps every rule in one place:

- names match ``[a-zA-Z_:][a-zA-Z0-9_:]*``, label names
  ``[a-zA-Z_][a-zA-Z0-9_]*``; a bad name is a programming error;
- label values escape backslash, double quote, and newline; HELP escapes
  backslash and newline;
- a value that is not finite is never written (invariant 1: an unknown
  value is absent, never ``NaN`` and never ``0``);
- at most ``MAX_SERIES`` samples and ``MAX_BYTES`` of text: a family that
  would cross either bound is left out with every family after it, and
  ``vakr_metrics_truncated`` says so.

Dependencies:
    dataclasses, enum, math, re: The model and the checks.

Safety:
    Writes only what the caller passes; the catalog keeps labels to fixed
    values (``vakr.metrics.catalog``).

Example:
    >>> fam = Family("vakr_up", Kind.GAUGE, "Always 1.", (Sample((), 1),))
    >>> print(render([fam]), end="")
    # HELP vakr_up Always 1.
    # TYPE vakr_up gauge
    vakr_up 1
    # HELP vakr_metrics_truncated 1 when the series or size cap left families out (E.33).
    # TYPE vakr_metrics_truncated gauge
    vakr_metrics_truncated 0
"""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
MAX_SERIES = 2000
"""Most samples one scrape answers; vakr's fixed label sets need about 400 (E.33)."""
MAX_BYTES = 512 * 1024
"""Largest scrape answer; about 40 KiB is typical (E.33)."""
_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_SUFFIXES = ("", "_sum", "_count")
_TRUNCATED = "vakr_metrics_truncated"
_TRUNCATED_HELP = "1 when the series or size cap left families out (E.33)."
_EXACT_INT = 2**53


class Kind(StrEnum):
    """A family's Prometheus type."""

    COUNTER = "counter"
    GAUGE = "gauge"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class Sample:
    """One series value.

    Attributes:
        labels: Label pairs in output order.
        value: The value; a non-finite one is never written.
        suffix: ``""``, or ``_sum`` / ``_count`` for a summary.
    """

    labels: tuple[tuple[str, str], ...]
    value: float
    suffix: str = ""

    def __post_init__(self) -> None:
        """Check the label names and the suffix.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If a label name is invalid or the suffix is unknown.
            TypeError: If a label value is not a string.
        """
        if self.suffix not in _SUFFIXES:
            raise ValueError(f"unknown sample suffix {self.suffix!r}")
        for name, value in self.labels:
            if not _LABEL.fullmatch(name) or name.startswith("__"):
                raise ValueError(f"invalid label name {name!r}")
            if not isinstance(value, str):
                raise TypeError(f"label {name} needs a string value")


@dataclass(frozen=True, slots=True)
class Family:
    """One metric family.

    Attributes:
        name: The metric name.
        kind: Counter, gauge, or summary.
        help: One line saying what it measures.
        samples: Its samples; a family without one is not written.
    """

    name: str
    kind: Kind
    help: str
    samples: tuple[Sample, ...]

    def __post_init__(self) -> None:
        """Check the name.

        Args:
            None

        Returns:
            None

        Raises:
            ValueError: If the name is invalid.
        """
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"invalid metric name {self.name!r}")


def escape_label(value: str) -> str:
    r"""Escape a label value.

    Args:
        value: Raw text.

    Returns:
        str: Text with ``\\``, ``"``, and newlines escaped.

    Raises:
        None
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_value(value: float) -> str:
    """Format a finite sample value; integers without a decimal point.

    Args:
        value: The value.

    Returns:
        str: Such as ``42``, ``0.25``, or ``1.5e-06``.

    Raises:
        ValueError: If the value is not finite.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("a sample value must be finite")
    if number.is_integer() and abs(number) < _EXACT_INT:
        return str(int(number))
    return repr(number)


def _lines(family: Family) -> list[str]:
    """Write one family's lines, leaving out non-finite samples.

    Args:
        family: The family.

    Returns:
        list[str]: Its lines; empty when no sample is finite.

    Raises:
        None
    """
    body = []
    for sample in family.samples:
        if isinstance(sample.value, bool) or not math.isfinite(float(sample.value)):
            continue
        labels = ",".join(f'{k}="{escape_label(v)}"' for k, v in sample.labels)
        braces = f"{{{labels}}}" if labels else ""
        body.append(f"{family.name}{sample.suffix}{braces} {format_value(sample.value)}")
    if not body:
        return []
    help_text = family.help.replace("\\", "\\\\").replace("\n", "\\n")
    return [f"# HELP {family.name} {help_text}", f"# TYPE {family.name} {family.kind}", *body]


def render(
    families: Iterable[Family], *, max_series: int = MAX_SERIES, max_bytes: int = MAX_BYTES
) -> str:
    """Write a scrape answer within the series and size caps.

    Args:
        families: The families, most important first.
        max_series: Most sample lines.
        max_bytes: Most bytes of text.

    Returns:
        str: The exposition text, ending with ``vakr_metrics_truncated``.

    Raises:
        None
    """
    out: list[str] = []
    series = size = 0
    truncated = 0
    for family in families:
        lines = _lines(family)
        count = len(lines) - 2 if lines else 0
        cost = sum(len(line.encode()) + 1 for line in lines)
        if series + count > max_series or size + cost > max_bytes - 256:  # room for the last
            truncated = 1
            break
        out.extend(lines)
        series, size = series + count, size + cost
    out.extend(_lines(Family(_TRUNCATED, Kind.GAUGE, _TRUNCATED_HELP, (Sample((), truncated),))))
    return "\n".join(out) + "\n"
