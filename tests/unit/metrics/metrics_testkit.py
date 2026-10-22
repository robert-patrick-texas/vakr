r"""A strict parser of Prometheus text format 0.0.4, for the self-metrics tests (E.33).

``parse`` accepts only what the format allows and what vakr promises on top:
every family has exactly one ``# HELP`` and one ``# TYPE`` before its
samples; samples of a family are contiguous; names and label names are
valid; label values are quoted and escaped; values are finite numbers;
no series repeats; every summary label set has quantiles, ``_sum``, and
``_count``. Anything else fails the test with the offending line.

Dependencies:
    re: The grammar.

Example:
    >>> parse("# HELP a An a.\n# TYPE a gauge\na 1\n")["a"].samples
    [('a', (), 1.0)]
"""

import math
import re
from dataclasses import dataclass, field

_NAME = r"[a-zA-Z_:][a-zA-Z0-9_:]*"
_SAMPLE = re.compile(rf"({_NAME})(?:\{{(.*)\}})? (\S+)")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\\n]|\\["\\n])*)"(,|$)')
_TYPES = {"counter", "gauge", "summary", "histogram", "untyped"}


@dataclass
class Parsed:
    """One parsed family.

    Attributes:
        name: Family name.
        kind: Its declared type.
        help: Its HELP text.
        samples: ``(sample name, labels, value)`` in order.
    """

    name: str
    kind: str = ""
    help: str = ""
    samples: list[tuple[str, tuple[tuple[str, str], ...], float]] = field(default_factory=list)

    def values(self) -> dict[tuple[tuple[str, str], ...], float]:
        """Return the base samples by label set.

        Args:
            None

        Returns:
            dict: Labels to value, for samples named like the family.

        Raises:
            None
        """
        return {labels: v for name, labels, v in self.samples if name == self.name}


def _labels(text: str, line: str) -> tuple[tuple[str, str], ...]:
    """Parse a label set strictly.

    Args:
        text: Between the braces.
        line: The whole line, for messages.

    Returns:
        tuple: Label pairs.

    Raises:
        AssertionError: If the syntax is wrong.
    """
    pairs, pos = [], 0
    while pos < len(text):
        m = _LABEL.match(text, pos)
        assert m is not None, f"bad labels: {line!r}"
        pairs.append((m.group(1), m.group(2)))
        pos = m.end()
    assert len({k for k, _ in pairs}) == len(pairs), f"repeated label: {line!r}"
    return tuple(pairs)


def _family_of(name: str, families: dict[str, Parsed]) -> Parsed | None:
    """Find the family a sample name belongs to (``_sum``/``_count`` of a summary).

    Args:
        name: Sample name.
        families: Families so far.

    Returns:
        Parsed | None: The family.

    Raises:
        None
    """
    if name in families:
        return families[name]
    for suffix in ("_sum", "_count"):
        base = name.removesuffix(suffix)
        if base != name and base in families and families[base].kind == "summary":
            return families[base]
    return None


def _check_summaries(families: dict[str, Parsed]) -> None:
    """Require quantiles, ``_sum``, and ``_count`` for every summary label set.

    Args:
        families: The parsed families.

    Returns:
        None

    Raises:
        AssertionError: If a part is missing.
    """
    for fam in families.values():
        if fam.kind != "summary":
            continue
        sets = {tuple(p for p in labels if p[0] != "quantile") for _, labels, _ in fam.samples}
        for base in sets:
            names = {n for n, labels, _ in fam.samples
                     if tuple(p for p in labels if p[0] != "quantile") == base}  # fmt: skip
            assert {fam.name, f"{fam.name}_sum", f"{fam.name}_count"} <= names, (fam.name, base)


def parse(text: str) -> dict[str, Parsed]:
    """Parse a whole scrape strictly.

    Args:
        text: The answer body.

    Returns:
        dict[str, Parsed]: Families by name.

    Raises:
        AssertionError: On any deviation from the format or vakr's promises.
    """
    assert text.endswith("\n"), "the answer must end with a newline"
    families: dict[str, Parsed] = {}
    order: list[str] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for line in text.splitlines():
        if line.startswith("# HELP "):
            name, _, help_text = line[7:].partition(" ")
            assert re.fullmatch(_NAME, name), line
            assert name not in families, line
            families[name] = Parsed(name, help=help_text)
            order.append(name)
        elif line.startswith("# TYPE "):
            name, _, kind = line[7:].partition(" ")
            assert name == order[-1], line
            assert kind in _TYPES, line
            assert not families[name].kind, line
            families[name].kind = kind
        else:
            m = _SAMPLE.fullmatch(line)
            assert m is not None, f"bad sample line: {line!r}"
            fam = _family_of(m.group(1), families)
            assert fam is not None, f"stray: {line!r}"
            assert fam.name == order[-1], f"stray: {line!r}"
            assert fam.kind, f"stray: {line!r}"
            labels = _labels(m.group(2) or "", line)
            value = float(m.group(3))
            assert math.isfinite(value), f"non-finite: {line!r}"
            assert (m.group(1), labels) not in seen, f"repeated series: {line!r}"
            seen.add((m.group(1), labels))
            fam.samples.append((m.group(1), labels, value))
    _check_summaries(families)
    return families
