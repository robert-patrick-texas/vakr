# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Name why a traffic window disagrees with MRTG (MVP-ACC-03: explained, not tuned away).

Every compared window outside the tolerance gets exactly one category,
the first that applies, in this order. Each one is a known way MRTG and
the application legitimately differ, and the report lists every window
with its category; only ``unexplained`` windows need a person's look.

Dependencies:
    dataclasses: Standard library only.

Safety:
    Pure classification.

Example:
    >>> explain(Evidence((100.0, 100.0), (100.0, 300.0), (100.0, 100.0), (100.0, 101.0), 1.0))
    'mrtg_repeat'
"""

from collections.abc import Callable
from dataclasses import dataclass, field

CATEGORIES = {
    "vakr_gap": "the application measured less than the required share of the window "
                "(a gap in its data; see MVP-ACC-10)",
    "mrtg_unknown": "MRTG has no value for the window (an unknown RRD row)",
    "ifindex_changed": "the device renumbered ifIndex; MRTG read another interface for part "
                       "of the window",
    "device_restart": "a counter discontinuity (reload, reset, or wrap the application did not "
                      "accept) in the window: the application leaves a gap, MRTG interpolates",
    "counter_wrap_32": "MRTG reads 32-bit counters, which wrap more than once per poll at this "
                       "rate, so MRTG under-counts",
    "maxbytes_clip": "the traffic exceeded MRTG's MaxBytes (or AbsMax), which MRTG discards",
    "mrtg_rounding": "within the 1 byte/s resolution of MRTG's .log files",
    "mrtg_repeat": "MRTG repeated its previous value, as it does after a failed poll, while the "
                   "traffic moved",
    "mrtg_unknown_as_zero": "MRTG logged 0 for an unknown poll (Options unknaszero)",
    "mrtg_run_jitter": "agrees when MRTG's poll is taken to have run up to the jitter allowance "
                       "earlier or later this time",
    "edge_burst": "traffic changed sharply within an application sample that straddles MRTG's "
                  "poll time, and that alone can account for the difference",
    "unexplained": "none of the above; needs a look",
}  # fmt: skip
"""Category names and what each one means, in the order they are tried."""

QUALITY_FLAGS = frozenset({
    "BASELINE_FIRST_SAMPLE", "BASELINE_MISSING_PRIOR", "BASELINE_GAP", "BASELINE_UPTIME_RESET",
    "BASELINE_DISCONTINUITY", "BASELINE_WIDTH_CHANGE", "BASELINE_DECREASE",
    "BASELINE_IDENTITY_UNRESOLVED", "BASELINE_ELAPSED_INVALID",
})  # fmt: skip
"""Sample flags that mean the application could not derive a rate for that interval."""
WRAP_BYTES = 2**32


@dataclass
class Evidence:
    """What is known about one window, in the application's orientation.

    Attributes:
        mrtg: MRTG's in/out bits per second.
        vakr: The application's in/out over MRTG's effective window.
        previous: MRTG's previous window, if any.
        vakr_previous: The application's value for that previous window.
        coverage: Share of the effective window the application measured.
        tolerance: Relative tolerance.
        flags: Quality flags of samples in the effective window.
        index_changed: Samples read from another ``ifIndex`` fall in it.
        peak: Highest in/out sample rate in it.
        limits: MRTG's in/out discard limits in bits/s.
        counter_bits: MRTG's counter width.
        step: Window length.
        unknaszero: MRTG logs unknown polls as 0.
        quantum: Rounding allowance in bits/s (0 for RRD data).
        jitter_agrees: Whether the window agrees with a nearby phase.
        edge: Edge uncertainty in/out, bits per second.
    """

    mrtg: tuple[float | None, float | None]
    vakr: tuple[float | None, float | None]
    previous: tuple[float | None, float | None] | None
    vakr_previous: tuple[float | None, float | None] | None
    coverage: float
    tolerance: float = 0.02
    flags: frozenset[str] = frozenset()
    index_changed: bool = False
    peak: tuple[float | None, float | None] = (None, None)
    limits: tuple[float | None, float | None] = (None, None)
    counter_bits: int = 64
    step: int = 300
    unknaszero: bool = False
    quantum: float = 0.0
    jitter_agrees: Callable[[], bool] = field(default=lambda: False)
    edge: tuple[float, float] = (0.0, 0.0)


def within(mrtg: float | None, vakr: float | None, tolerance: float, slack: float = 0.0) -> bool:
    """Report whether two rates agree.

    Args:
        mrtg: MRTG's value.
        vakr: The application's value.
        tolerance: Relative tolerance of MRTG's value.
        slack: Extra absolute allowance.

    Returns:
        bool: True when both are known and ``|vakr - mrtg| <= tolerance * mrtg + slack``.

    Raises:
        None
    """
    if mrtg is None or vakr is None:
        return False
    return abs(vakr - mrtg) <= tolerance * abs(mrtg) + slack


def _failing(e: Evidence) -> list[int]:
    """List the directions (0 in, 1 out) outside the tolerance.

    Args:
        e: The evidence.

    Returns:
        list[int]: Failing directions.

    Raises:
        None
    """
    return [d for d in (0, 1) if not within(e.mrtg[d], e.vakr[d], e.tolerance)]


def _limit_hit(e: Evidence) -> bool:
    """Report whether the traffic exceeded MRTG's discard limit in a failing direction.

    Args:
        e: The evidence.

    Returns:
        bool: True when a peak sample rate is above the limit.

    Raises:
        None
    """
    return any(
        (limit := e.limits[d]) is not None and (peak := e.peak[d]) is not None and peak > limit
        for d in (0, 1)
    )


def _repeated(e: Evidence) -> bool:
    """Report whether MRTG carried its previous value over (a failed poll, no ``unknaszero``).

    Args:
        e: The evidence.

    Returns:
        bool: True when MRTG's value equals its previous window's exactly and
        that previous value matched the application's previous window, so the
        traffic moved but MRTG did not.

    Raises:
        None
    """
    if e.previous is None or e.previous != e.mrtg or not any(e.mrtg):
        return False
    before = e.vakr_previous or (None, None)
    return all(within(e.previous[d], before[d], e.tolerance) for d in (0, 1))


def explain(e: Evidence) -> str:
    """Name the first category that applies to a window outside the tolerance.

    Args:
        e: The evidence.

    Returns:
        str: A ``CATEGORIES`` key.

    Raises:
        None
    """
    bad = _failing(e)
    wrap_bps = WRAP_BYTES * 8 / e.step
    checks: list[tuple[str, Callable[[], bool]]] = [
        ("ifindex_changed", lambda: e.index_changed),
        ("device_restart", lambda: bool(e.flags & QUALITY_FLAGS)),
        ("counter_wrap_32", lambda: e.counter_bits == 32 and any(
            (p or 0) >= wrap_bps or "COUNTER_WRAP_32" in e.flags for p in e.peak)),
        ("maxbytes_clip", lambda: _limit_hit(e)),
        ("mrtg_rounding", lambda: e.quantum > 0 and all(
            within(e.mrtg[d], e.vakr[d], 0.0, e.quantum) for d in bad)),
        ("mrtg_repeat", lambda: _repeated(e)),
        ("mrtg_unknown_as_zero", lambda: e.unknaszero and all(
            e.mrtg[d] == 0 and (e.vakr[d] or 0) > 0 for d in bad)),
        ("mrtg_run_jitter", e.jitter_agrees),
        ("edge_burst", lambda: all(
            within(e.mrtg[d], e.vakr[d], e.tolerance, e.edge[d]) for d in bad)),
    ]  # fmt: skip
    return next((name for name, test in checks if test()), "unexplained")
