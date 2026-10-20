"""Property test: no reset or discontinuity sequence ever yields an impossible rate (RATE-004).

A simulated interface carries traffic at no more than its line rate. Between
polls, any of these may happen: nothing, a management-agent restart (counters
restart and uptime shows a reset), an interface counter discontinuity (the
counter jumps anywhere and ``ifCounterDiscontinuityTime`` changes), a silent
counter reset with no evidence at all, an HC/32-bit family switch, or a gap
longer than the baseline allows. Hypothesis generates long sequences of these.

The rate pipeline must never report a rate above ``1.10 x line rate`` (the
plausibility ceiling), must report the exact true rate whenever nothing
happened, and must report no rate across any event it has evidence for.

Dependencies:
    hypothesis: Generated event sequences, derandomized for CI.
    vakr.logic.rates, vakr.logic.baseline: Under test.

Example:
    $ uv run pytest tests/unit/logic/test_logic_rates_property.py
"""

from dataclasses import dataclass, field

import hypothesis
from hypothesis import given, settings
from hypothesis import strategies as st

from vakr.domain.enums import CounterFamily, UptimeContinuity
from vakr.domain.quality import Quality
from vakr.logic.baseline import RateContext, max_gap_s
from vakr.logic.capacity import CapacityInputs
from vakr.logic.counters import CounterSample
from vakr.logic.rates import compute_rates

PROPERTY = settings(derandomize=True, database=None, max_examples=300, deadline=None)
INTERVAL = 60.0
EVENTS = ("none", "none", "none", "agent_restart", "discontinuity", "silent_reset", "switch")
STEP = st.tuples(
    st.sampled_from(EVENTS),
    st.integers(min_value=1, max_value=400),  # elapsed seconds, sometimes beyond the gap
    st.integers(min_value=0, max_value=1000),  # utilization in tenths of a percent
    # Where a discontinuity jumps to. HC counters never come near 2^64 in practice
    # (about 47 years at 100 Gb/s), so a 64-bit decrease is always a reset.
    st.integers(min_value=0, max_value=2**63),
)
# Starting near the 32-bit top makes wraps happen within a few intervals.
START = st.one_of(st.integers(2**32 - 2 * 10**9, 2**32 - 1), st.integers(0, 2**63))


@dataclass
class Interface:
    """A simulated interface: the true octet count and what the agent reports.

    Attributes:
        family: Counter family the agent reports now.
        counter: Octets counted since the agent's counters last restarted.
        discontinuity: Current ``ifCounterDiscontinuityTime``.
        history: Previous samples, newest last.
    """

    family: CounterFamily
    counter: int = 0
    discontinuity: int = 0
    history: list[CounterSample] = field(default_factory=list)

    def observe(self) -> CounterSample:
        """Read the counter as the agent would report it, wrapped to its width.

        Args:
            None

        Returns:
            CounterSample: In and out octets carry the same value.

        Raises:
            None
        """
        width = 64 if self.family is CounterFamily.HC64 else 32
        value = self.counter % 2**width
        return CounterSample(self.family, value, value, discontinuity_ticks=self.discontinuity)


def advance(iface: Interface, event: str, octets: int, jump: int) -> UptimeContinuity:
    """Apply one event and the interval's traffic, and report uptime continuity.

    Args:
        iface: The interface, updated in place.
        event: What happened during the interval.
        octets: True octets carried during the interval.
        jump: Arbitrary counter value for a discontinuity.

    Returns:
        UptimeContinuity: RESET_DETECTED after an agent restart, else CONTINUOUS.

    Raises:
        None
    """
    continuity = UptimeContinuity.CONTINUOUS
    if event == "agent_restart":
        iface.counter, iface.discontinuity = 0, 0
        continuity = UptimeContinuity.RESET_DETECTED
    elif event == "discontinuity":
        iface.counter, iface.discontinuity = jump, iface.discontinuity + 1
    elif event == "silent_reset":
        iface.counter = 0
    elif event == "switch":
        iface.family = (
            CounterFamily.HC64 if iface.family is CounterFamily.LEGACY32 else CounterFamily.LEGACY32
        )
    # Restarted counters only see the traffic after the restart: at most the interval's.
    iface.counter += octets
    return continuity


@PROPERTY
@given(
    st.sampled_from([CounterFamily.HC64, CounterFamily.LEGACY32]),
    st.sampled_from([10**6, 10**7, 10**8, 10**9, 10**10]),
    START,
    st.lists(STEP, min_size=1, max_size=40),
)
def test_no_sequence_yields_a_rate_above_the_possible_maximum(
    family: CounterFamily, line_rate: int, start: int, steps: list[tuple[str, int, int, int]]
) -> None:
    """RATE-004, RATE-005: gaps instead of spikes, and exact rates when nothing happened.

    Args:
        family: Starting counter family.
        line_rate: Interface speed in b/s, reported through ``ifHighSpeed``.
        start: Counter value before the first sample.
        steps: Generated (event, elapsed, utilization, jump) per interval.

    Returns:
        None

    Raises:
        None
    """
    capacity = CapacityInputs(if_high_speed_mbps=line_rate // 10**6)
    iface = Interface(family, start)
    prev = iface.observe()
    for event, elapsed, permille, jump in steps:
        octets = line_rate * elapsed * permille // 8000  # never above line rate
        before = iface.family
        continuity = advance(iface, event, octets, jump)
        curr = iface.observe()
        r = compute_rates(prev, curr, RateContext(float(elapsed), INTERVAL, continuity, capacity))
        rate = r.in_octets.rate
        if rate is not None:
            assert rate <= 1.10 * line_rate
        if event in ("agent_restart", "discontinuity", "switch"):
            assert rate is None
        gap = max_gap_s(before, INTERVAL, line_rate)
        if event == "none" and elapsed <= gap and before is iface.family:
            assert rate == octets * 8 / elapsed
            hypothesis.event(f"exact {before.value} rate checked")
        if Quality.COUNTER_WRAP_32 in r.quality:
            hypothesis.event("32-bit wrap accepted")
        if event == "silent_reset" and before is CounterFamily.HC64 and rate is not None:
            assert rate <= octets * 8 / elapsed  # an undetected reset can only undercount
        prev = curr
