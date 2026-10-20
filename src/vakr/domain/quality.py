"""Quality flags: explicit, combinable evidence attached to every observation.

A value without context invites false certainty: an interface rate after a
router reload, a ping result from an overloaded collector, or a sample that
missed half its varbinds all look like ordinary numbers. Quality flags record
that context so storage keeps it, graphs show it, and rankings can exclude
it (blueprint 4.3, RATE-003, SCHED-017, UI-015).

The bit positions are part of the language-neutral observation contract
(ARCH-002): a future Go collector writes the same integers. Positions are
append-only — never renumber or reuse one.

Dependencies:
    enum: ``IntFlag`` gives combinable flags that store as one integer.

Safety:
    Flags describe evidence; they never replace a missing value with a
    default. A rate with ``BASELINE_GAP`` is stored as unavailable, not zero.

Example:
    >>> flags = Quality.PARTIAL_LOSS | Quality.RETRIED
    >>> Quality.PARTIAL_LOSS in flags, int(Quality.PARTIAL_LOSS)
    (True, 1)
    >>> Quality.BASELINE_GAP in BASELINE_INVALIDATING
    True
"""

from enum import IntFlag


class Quality(IntFlag):
    """Combinable evidence about one observation; stored as a 64-bit integer.

    Members are grouped by concern. Each comment names the requirement or
    Appendix E rule that sets it.
    """

    NONE = 0
    # Reachability (Appendix E.1)
    PARTIAL_LOSS = 1 << 0  # 4 of 5 probes answered
    SINGLE_REPLY = 1 << 1  # exactly 1 of 5 answered; SNMP skipped under icmp_gated
    # Collection context (SCHED-017, OBS-003)
    NOT_ATTEMPTED = 1 << 2  # capacity or scheduler prevented the poll
    COLLECTOR_DEGRADED = 1 << 3  # collector health makes the result unknown
    STALE = 1 << 4  # older than the freshness threshold (E.3)
    # SNMP response completeness (SNMP-014, DISC-008)
    PARTIAL_RESPONSE = 1 << 5  # some varbinds or subtables failed
    MISSING_FIELDS = 1 << 6  # specific fields absent from an otherwise good reply
    # Counter baselines (RATE-003, section 9.7)
    BASELINE_FIRST_SAMPLE = 1 << 7
    BASELINE_MISSING_PRIOR = 1 << 8
    BASELINE_GAP = 1 << 9  # elapsed time over the maximum gap (E.4)
    BASELINE_UPTIME_RESET = 1 << 10
    BASELINE_DISCONTINUITY = 1 << 11  # ifCounterDiscontinuityTime changed
    BASELINE_WIDTH_CHANGE = 1 << 12  # HC <-> 32-bit counter family switch
    BASELINE_DECREASE = 1 << 13  # unexplained counter decrease
    BASELINE_IDENTITY_UNRESOLVED = 1 << 14
    COUNTER_WRAP_32 = 1 << 15  # a plausible 32-bit wrap was accepted (RATE-005)
    # Rates and capacity (RATE-008..010)
    RATE_EXCEEDS_CAPACITY = 1 << 16  # kept, not clipped (E.4)
    CAPACITY_UNKNOWN = 1 << 17  # absolute rates only; utilization unavailable
    # Uptime and time (E.5, MPOL-005)
    UPTIME_AHEAD = 1 << 18
    CLOCK_UNSYNCED = 1 << 19
    # Administrative and transport context
    MAINTENANCE = 1 << 20  # collected while the device is in maintenance
    REQUEST_SIZE_REDUCED = 1 << 21  # tooBig led to smaller PDUs (SNMP-017)
    RETRIED = 1 << 22  # more than one attempt was needed (SNMP-018)
    # Counter baselines, continued: bits are append-only, so it cannot join its group.
    BASELINE_ELAPSED_INVALID = 1 << 23  # non-positive elapsed time between samples


BASELINE_INVALIDATING = (
    Quality.BASELINE_FIRST_SAMPLE
    | Quality.BASELINE_MISSING_PRIOR
    | Quality.BASELINE_GAP
    | Quality.BASELINE_UPTIME_RESET
    | Quality.BASELINE_DISCONTINUITY
    | Quality.BASELINE_WIDTH_CHANGE
    | Quality.BASELINE_DECREASE
    | Quality.BASELINE_IDENTITY_UNRESOLVED
    | Quality.BASELINE_ELAPSED_INVALID
)
"""Flags that mean derived rates are unavailable for the sample (RATE-004)."""

UNKNOWN_CONTEXT = Quality.NOT_ATTEMPTED | Quality.COLLECTOR_DEGRADED | Quality.STALE
"""Flags that make a state unknown rather than failed (OBS-002, E.2 row 3)."""
