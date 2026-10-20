"""Turn one Stage 2 sample into an interface observation with rates (9.7, RATE-001..010).

For each sampled interface the collector looks up the previous accepted
sample of the same interface from this collector (``BaselineStore``), hands
both to ``vakr.logic.rates.compute_rates`` with the actual elapsed time,
the device's uptime continuity, and the interface's capacity inputs, and
builds the ``InterfaceObservation``. The logic decides; this module only
supplies the evidence and keeps the store current:

* the new sample becomes the next baseline whatever the verdict (RATE-004),
  unless it holds no counter at all, because an unanswered request is a gap,
  not a new starting point: the older baseline stays, and the E.4 gap rule
  still bounds how old it may be;
* a baseline read from a different ``ifIndex`` is not used: the counters
  belong to another row of the agent's table (``BASELINE_MISSING_PRIOR``).

Dependencies:
    vakr.logic.baseline, vakr.logic.rates: The rate rules.
    vakr.collectors.snmp: Samples and the observation builder.
    vakr.app.collector.baselines: The store.

Safety:
    Pure apart from updating the store. Requires the persistent interface
    UUID; callers never pass an unresolved interface (invariant 6).

Example:
    >>> DeriveContext.__name__
    'DeriveContext'
"""

from dataclasses import dataclass

from vakr.app.collector.baselines import Baseline, BaselineStore
from vakr.collectors.snmp import InterfaceSample, build_interface_observation
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import UptimeContinuity
from vakr.domain.ids import CollectorId, DeviceId, PollId
from vakr.domain.observations import InterfaceObservation
from vakr.logic.baseline import MAX_GAP_INTERVALS, PLAUSIBILITY_FACTOR, RateContext
from vakr.logic.capacity import CapacityInputs
from vakr.logic.counters import COUNTERS
from vakr.logic.rates import compute_rates


@dataclass(frozen=True, slots=True)
class RateSettings:
    """The configurable rate limits (``[rates]``, Appendix E.4).

    Attributes:
        max_gap_intervals: Longest usable gap, in effective intervals (RATE-003).
        plausibility_factor: Rates above this multiple of capacity are flagged (RATE-009).
    """

    max_gap_intervals: float = MAX_GAP_INTERVALS
    plausibility_factor: float = PLAUSIBILITY_FACTOR

    @classmethod
    def from_config(cls, config: EffectiveConfig) -> RateSettings:
        """Read the ``[rates]`` options.

        Args:
            config: The validated configuration.

        Returns:
            RateSettings: The limits; catalog defaults when unset.

        Raises:
            ValueError: If a value is not a number.
        """
        return cls(
            float(config.get("rates.max_gap_intervals", MAX_GAP_INTERVALS)),
            float(config.get("rates.plausibility_factor", PLAUSIBILITY_FACTOR)),
        )


@dataclass(frozen=True, slots=True)
class DeriveContext:
    """Evidence shared by every interface of one poll.

    Attributes:
        poll_id: The poll.
        collector_id: This collector.
        device_id: The device.
        interval_s: The device's effective interval, for the E.4 gap rule.
        continuity: ``sysUpTime`` continuity since the previous poll (E.5).
        boot_id: This kernel boot, which scopes monotonic time.
        limits: The configurable gap and plausibility limits.
    """

    poll_id: PollId
    collector_id: CollectorId
    device_id: DeviceId
    interval_s: float
    continuity: UptimeContinuity
    boot_id: str
    limits: RateSettings = RateSettings()


def derive_observation(
    sample: InterfaceSample, store: BaselineStore, capacity: CapacityInputs, ctx: DeriveContext
) -> InterfaceObservation:
    """Derive rates for one sample, build its observation, and advance the baseline.

    Args:
        sample: The Stage 2 sample; its ``interface_id`` must be set.
        store: This collector's baselines.
        capacity: The interface's capacity inputs.
        ctx: Poll-wide evidence.

    Returns:
        InterfaceObservation: Raw counters, and rates when the baseline was valid.

    Raises:
        ValueError: If the sample has no persistent interface UUID.
    """
    iid = sample.interface_id
    if iid is None:
        raise ValueError("an interface observation needs the persistent interface UUID")
    prev = store.get(iid)
    moved = prev is not None and prev.if_index != sample.if_index
    if moved:
        prev = None
    elapsed = None
    if prev is not None:
        elapsed = prev.elapsed_to(sample.sampled_at, sample.sampled_monotonic, ctx.boot_id)
    limits = ctx.limits
    rate_ctx = RateContext(
        elapsed, ctx.interval_s, ctx.continuity, capacity, moved,
        limits.max_gap_intervals, limits.plausibility_factor,
    )  # fmt: skip
    current = sample.counter_sample()
    rates = compute_rates(None if prev is None else prev.sample, current, rate_ctx)
    observation = build_interface_observation(
        sample, rates, poll_id=ctx.poll_id, collector_id=ctx.collector_id,
        device_id=ctx.device_id, elapsed_s=elapsed,
    )  # fmt: skip
    if any(current.value(name) is not None for name in COUNTERS):
        at, mono = sample.sampled_at, sample.sampled_monotonic
        store.put(iid, Baseline(ctx.device_id, sample.if_index, current, at, mono, ctx.boot_id))
    return observation
