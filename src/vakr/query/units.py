"""SI display units for graph axes (12.6: ``kbit/s``, ``Mbit/s``, ``Gbit/s``).

Values in a view stay in their base unit (bits per second, packets per
second, microseconds), so nothing is rounded on the way to the client. The
display unit is a suggestion: the largest SI prefix (powers of 1,000, as
link speeds are quoted) that keeps the graph's largest magnitude at or
above 1. Every series of one graph shares the unit, so inbound and
outbound stay comparable. A graph with no values gets the base unit.

Dependencies:
    vakr.query.history_views: ``UnitView``.

Safety:
    Pure.

Example:
    >>> scaled("bit/s", [950.0, 1_234_000.0]).display
    'Mbit/s'
    >>> scaled("bit/s", []).display, fixed("us", "ms", 1000.0).divisor
    ('bit/s', 1000.0)
    >>> si_text(1_000_000_000, BITS), si_text(150_000_000, BITS)
    ('1 Gbit/s', '150 Mbit/s')
"""

from collections.abc import Iterable

from vakr.query.history_views import UnitView

_PREFIXES = ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"))

BITS = "bit/s"
PACKETS = "pkt/s"
ERRORS = "err/s"
PERCENT = "%"
MICROSECONDS = "us"


def scaled(base: str, values: Iterable[float | None]) -> UnitView:
    """Choose the SI prefix for a rate unit from the largest magnitude present.

    Args:
        base: Base unit, such as ``bit/s``.
        values: Every value and envelope bound in the graph; ``None`` skipped.

    Returns:
        UnitView: The base unit and the suggested display unit.

    Raises:
        None
    """
    peak = max((abs(v) for v in values if v is not None), default=0.0)
    for factor, prefix in _PREFIXES:
        if peak >= factor:
            return UnitView(base, f"{prefix}{base}", factor)
    return UnitView(base, base, 1.0)


def fixed(base: str, display: str, divisor: float) -> UnitView:
    """Return a unit that does not depend on the data, such as microseconds shown as ms.

    Args:
        base: Base unit.
        display: Display unit.
        divisor: ``display = base / divisor``.

    Returns:
        UnitView: The unit.

    Raises:
        None
    """
    return UnitView(base, display, divisor)


def si_text(value: float, base: str) -> str:
    """Format one quantity with its SI prefix, three significant digits.

    Args:
        value: The quantity in the base unit.
        base: Base unit, such as ``bit/s``.

    Returns:
        str: Such as ``"1 Gbit/s"`` or ``"155 Mbit/s"``.

    Raises:
        None
    """
    unit = scaled(base, [value])
    return f"{value / unit.divisor:.3g} {unit.display}"
