"""One position's latency graph: smoke, median, jitter, and loss (12.6, MPOS-008, RET-006).

For each collector separately, ``position_latency`` reads the median RTT,
loss, and jitter series by the graph's plan and, when the plan is raw (a
window of at most 1,200 polls), every probe of every poll as the "smoke".
``notes_for`` states what the distribution and the loss mean at the
resolution actually used, so a reader never mistakes a band of medians for
individual probes, or an averaged loss for a counted one.

Dependencies:
    vakr.ports.telemetry: Series and raw reads.
    vakr.query.history_*, latency_views: Context, plans, reads, and views.

Safety:
    Bounded by the window, the point cap, and the probe cap. Lost probes
    stay ``None``; they are never a zero RTT (ICMP-005).

Example:
    >>> notes_for(raw=True)[0].startswith("Smoke: every probe")
    True
"""

from datetime import datetime

from vakr.domain.catalog import Collector
from vakr.domain.enums import ObservationType
from vakr.domain.ids import CollectorId, DeviceId
from vakr.domain.observations import IcmpObservation
from vakr.domain.series import Metric, TimeWindow
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.history_context import Subject, choose_collectors, interval_of
from vakr.query.history_fetch import SeriesAsk, read_series
from vakr.query.history_markers import flag_names
from vakr.query.history_requests import GraphRequest, Plan, plan
from vakr.query.latency_views import PositionLatency, SmokeMode, SmokePoint
from vakr.query.settings import QuerySettings
from vakr.query.snapshot import pages

PROBES_NOTE = (
    "Smoke: every probe's round-trip time of every poll; a lost probe has no value "
    "and is counted in the loss."
)
ENVELOPE_NOTE = (
    "Smoke: each point is one bucket: the mean of the per-poll median RTTs, with the lowest "
    "and highest per-poll median as the band. Individual probes are kept for the native "
    "retention period and shown for windows of up to 1,200 polls."
)
ROLLUP_NOTE = (
    "Beyond the native horizon, 15-minute rollups keep the minimum, mean, 95th percentile, "
    "and maximum of the per-poll median RTT and the summed probe counts; individual probes "
    "are not kept, and the band shows the lowest and highest bucket values."
)
LOSS_NOTE = (
    "Loss in a bucket: rollups compute it from summed probe counts (RET-008); native buckets "
    "average per-poll loss, which equals the counted loss when every poll sent the same "
    "number of probes (5 by default)."
)
POSITIONS_NOTE = "Each position is its own series; RTT, loss, and jitter are never merged."


def notes_for(*, raw: bool) -> tuple[str, ...]:
    """Return the notes that explain a latency view at its resolution.

    Args:
        raw: Whether the plan was raw (per-poll points and probes).

    Returns:
        tuple[str, ...]: Operator explanations.

    Raises:
        None
    """
    if raw:
        return (PROBES_NOTE, POSITIONS_NOTE)
    return (ENVELOPE_NOTE, ROLLUP_NOTE, LOSS_NOTE, POSITIONS_NOTE)


def read_probes(
    backend: TelemetryBackend,
    device: DeviceId,
    collector: CollectorId,
    window: TimeWindow,
    cap: int,
) -> tuple[tuple[SmokePoint, ...], bool]:
    """Read every probe cycle of one position in the window, oldest first.

    Args:
        backend: The telemetry view.
        device: The device.
        collector: The position's collector.
        window: The window.
        cap: Most polls to return.

    Returns:
        tuple: The smoke points, and True when more polls existed.

    Raises:
        StorageError: If the store cannot be read.
    """
    found, more = pages(
        lambda p: backend.query.read_raw(
            ObservationType.ICMP, device, window, p, collector_id=collector
        ),
        cap,
    )
    points = tuple(
        SmokePoint(o.timestamp, o.rtts_us, o.median_rtt_us, o.loss_pct, flag_names(int(o.quality)))
        for o in found
        if isinstance(o, IcmpObservation)
    )
    return points, more


def position_latency(
    backend: TelemetryBackend,
    device: DeviceId,
    collector: Collector,
    window: TimeWindow,
    how: Plan,
    cap: int,
    interval_s: float,
) -> PositionLatency:
    """Build one position's latency evidence.

    Args:
        backend: The telemetry view.
        device: The device.
        collector: The position.
        window: The window.
        how: The graph's plan.
        cap: Point cap per series, and the probe cap in ``probes`` mode.
        interval_s: The position's effective poll interval.

    Returns:
        PositionLatency: Smoke, median, loss, and jitter for this position.

    Raises:
        StorageError: If the store cannot be read.
    """

    def ask(metric: Metric, label: str) -> SeriesAsk:
        """Describe one ICMP series of this position.

        Args:
            metric: The metric.
            label: Legend text.

        Returns:
            SeriesAsk: The request.

        Raises:
            None
        """
        return SeriesAsk(device, collector, metric, label, interval_s)

    median = read_series(backend, ask(Metric.RTT_MEDIAN_US, "Median RTT"), window, how, cap)
    loss = read_series(backend, ask(Metric.LOSS_PCT, "Loss"), window, how, cap)
    jitter = read_series(backend, ask(Metric.JITTER_US, "Jitter"), window, how, cap)
    if how.raw:
        probes, more = read_probes(backend, device, collector.id, window, cap)
        mode = SmokeMode.PROBES
    else:
        probes, more, mode = (), False, SmokeMode.MEDIAN_ENVELOPE
    return PositionLatency(
        collector.id, collector.position, mode, probes, more, median, loss, jitter
    )


def latency_positions(
    backend: TelemetryBackend,
    subject: Subject,
    request: GraphRequest,
    window: TimeWindow,
    settings: QuerySettings,
    now: datetime,
) -> tuple[list[PositionLatency], bool]:
    """Read every chosen position's latency evidence, each separately (MPOS-008).

    Args:
        backend: The telemetry view.
        subject: The device's inventory.
        request: The request (collector, source, point cap).
        window: The resolved window.
        settings: Defaults.
        now: Reference time (UTC).

    Returns:
        tuple: One entry per position, and whether every plan was raw.

    Raises:
        StorageError: If the store cannot be read.
        ValidationError: If the store refuses the request.
    """
    chosen = choose_collectors(subject.collectors, request.collector_id, "", every=True)
    horizon, rollups = backend.retention.native_horizon(), backend.capabilities.rollups
    found, raw = [], True
    for c in chosen:
        interval = interval_of(backend, subject.device, c, settings, now)
        how = plan(
            window, request.source, interval, request.max_points, horizon=horizon, rollups=rollups
        )
        raw = raw and how.raw
        found.append(
            position_latency(
                backend, subject.device.id, c, window, how, request.max_points, interval
            )
        )
    return found, raw
