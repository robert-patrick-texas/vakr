"""View models for latency "smoke" graphs and interface state timelines (12.4, 12.5, 12.6).

A latency graph shows, per position and never merged (MPOS-008, 27.5 #6),
the RTT distribution ("smoke"), the median line, and loss. What the
distribution can be depends on what is stored:

- ``probes``: every probe's RTT of every poll, lost probes as ``None``.
  Native observations keep each probe (ICMP-003), so this is available for
  windows within the native horizon that hold at most 1,200 polls (about
  20 hours at the 60 s default).
- ``median_envelope``: longer windows are bucketed; each bucket carries the
  mean of the per-poll median RTTs with the lowest and highest per-poll
  median as its band. Beyond the native horizon this comes from 15-minute
  rollups, which keep the min, mean, p95, and max of the per-poll median
  and the summed probe counts (RET-006); individual probes are not kept.

A state timeline shows an interface's ``admin/oper`` status as segments
from native samples (rollups keep no status).

Dependencies:
    vakr.query.history_views: Series, unit, and window views.

Safety:
    Measurements and identifiers only.

Example:
    >>> SmokeMode.PROBES.value, SmokeMode.MEDIAN_ENVELOPE.value
    ('probes', 'median_envelope')
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from vakr.domain.ids import CollectorId, DeviceId
from vakr.query.history_views import SeriesView, UnitView, WindowView


class SmokeMode(StrEnum):
    """Which RTT distribution a latency view carries."""

    PROBES = "probes"
    MEDIAN_ENVELOPE = "median_envelope"


@dataclass(frozen=True, slots=True)
class SmokePoint:
    """One poll's probes (12.6 "smoke").

    Attributes:
        t: When the probe cycle started (UTC).
        rtts_us: Every probe's RTT in send order, microseconds; ``None`` = lost.
        median_us: The poll's median RTT, if any probe answered.
        loss_pct: Lost probes as a percentage of probes sent.
        flags: Quality flag names, such as ``partial_loss``.
    """

    t: datetime
    rtts_us: tuple[int | None, ...]
    median_us: int | None
    loss_pct: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PositionLatency:
    """One position's latency evidence; positions are never merged (MPOS-008).

    Attributes:
        collector_id: The collector.
        position: Its position label.
        smoke_mode: What the distribution is (see the module docstring).
        probes: Per-poll probes in ``probes`` mode, else empty.
        probes_truncated: True when more polls existed than were returned.
        median: Median RTT series (``us``), with the envelope when bucketed.
        loss: Loss percentage series.
        jitter: Jitter series (``us``).
    """

    collector_id: CollectorId
    position: str
    smoke_mode: SmokeMode
    probes: tuple[SmokePoint, ...]
    probes_truncated: bool
    median: SeriesView
    loss: SeriesView
    jitter: SeriesView


@dataclass(frozen=True, slots=True)
class LatencyView:
    """A device's reachability graph: smoke, median, and loss per position (12.4, 12.6).

    Attributes:
        device_id: The device.
        device: Its display name.
        window: Window and zone.
        source: Requested source: ``auto``, ``native``, or ``rollup``.
        rtt_unit: Unit of RTT and jitter values (``us``, shown as ``ms``).
        loss_unit: Unit of loss values (``%``).
        positions: One entry per collector, in position order.
        truncated: True when any series or probe list hit its cap.
        available: False when nothing can be drawn at all.
        reason: Why not, in operator words.
        generated_at: When the view was computed (UTC).
        notes: What the distribution and loss mean at this resolution.
    """

    device_id: DeviceId
    device: str
    window: WindowView
    source: str
    rtt_unit: UnitView
    loss_unit: UnitView
    positions: tuple[PositionLatency, ...]
    truncated: bool
    available: bool
    reason: str | None
    generated_at: datetime
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StateSegment:
    """A stretch with one ``admin/oper`` status, or without samples.

    Attributes:
        start: Segment start (UTC).
        end: Segment end (UTC).
        admin: ``ifAdminStatus`` name; ``None`` when unknown or not reported.
        oper: ``ifOperStatus`` name; ``None`` when unknown or not reported.
        samples: Samples in the segment; 0 for a stretch without samples.
    """

    start: datetime
    end: datetime
    admin: str | None
    oper: str | None
    samples: int


@dataclass(frozen=True, slots=True)
class TimelineView:
    """An interface's status timeline from one collector (12.5).

    Attributes:
        interface_id: The interface.
        subject: Display text, such as ``core1 Gi0/0/1``.
        window: Window and zone (at most the last 7 days of the request).
        collector_id: The collector whose samples are shown, if any.
        position: Its position label.
        segments: Segments in time order.
        truncated: True when more samples existed than one timeline reads.
        available: False when nothing can be shown at all.
        reason: Why not, in operator words.
        generated_at: When the view was computed (UTC).
        notes: Standing explanations.
    """

    interface_id: UUID
    subject: str
    window: WindowView
    collector_id: CollectorId | None
    position: str
    segments: tuple[StateSegment, ...]
    truncated: bool
    available: bool
    reason: str | None
    generated_at: datetime
    notes: tuple[str, ...] = ()
