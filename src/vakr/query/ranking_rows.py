"""Ranking rows from one window scan: the E.17 #13 and #15 rules (DASH-003..005).

Each function takes the interfaces in scope and the scan's per-interface
summaries and returns ranked rows, the excluded interfaces (capped, with
the full count), and how many interfaces were candidates. Ties in score
break in natural device-then-interface name order, then UUID, so rankings
are stable.

Dependencies:
    vakr.logic.ranking: Expected samples and exclusion words.
    vakr.ports.telemetry.rankings: Scan results.
    vakr.query.*: Index, scope, capacity, and views.

Safety:
    Pure; unavailable samples reduce coverage and never lower a score.

Example:
    >>> per_million(4, 2_000_000), per_million(4, 0), per_million(4, None)
    (2.0, None, None)
"""

from dataclasses import replace

from vakr.domain.interfaces import Interface
from vakr.domain.series import TimeWindow
from vakr.logic.ranking import NO_BASELINE, expected_samples
from vakr.ports.telemetry.rankings import RankingScan, WindowSummary
from vakr.query.catalog_index import CatalogIndex
from vakr.query.catalog_scope import interface_ref, ref_key
from vakr.query.history_limits import EXCLUDED_SHOWN
from vakr.query.interface_capacity import capacity_of, no_capacity_reason
from vakr.query.ranking_views import (
    CoverageView,
    ErrorRow,
    ExcludedRow,
    RankingRequest,
    UtilizationRow,
)
from vakr.query.settings import QuerySettings

type Excluded = tuple[tuple[ExcludedRow, ...], int]


def per_million(errors: int, packets: int | None) -> float | None:
    """Return errors per million packets, when packets are known and moved (E.17 #13).

    Args:
        errors: Error count.
        packets: Packet count over the same samples, if known.

    Returns:
        float | None: The ratio, or ``None``.

    Raises:
        None
    """
    return errors * 1_000_000 / packets if packets else None


def _expected(index: CatalogIndex, iface: Interface, window_s: float, s: QuerySettings) -> int:
    """Return how many samples the window should hold for an interface's device.

    Args:
        index: The inventory.
        iface: The interface.
        window_s: Window length in seconds.
        s: Settings, for the default interval.

    Returns:
        int: Whole intervals in the window, at least 1.

    Raises:
        None
    """
    device = index.devices.get(iface.device_id)
    interval = (device.poll_interval_s if device else None) or s.default_interval_s
    return expected_samples(window_s, interval)


def _finish[R: (UtilizationRow, ErrorRow)](
    ranked: list[R], excluded: list[ExcludedRow], limit: int
) -> tuple[tuple[R, ...], Excluded]:
    """Number the top rows and cap the excluded list.

    Args:
        ranked: Rows in rank order, numbered 0.
        excluded: Excluded interfaces in any order.
        limit: Rows to keep.

    Returns:
        tuple: Numbered rows, and ``(shown excluded, excluded total)``.

    Raises:
        None
    """
    rows = tuple(replace(r, rank=n) for n, r in enumerate(ranked[:limit], start=1))
    excluded.sort(key=lambda x: ref_key(x.interface))
    return rows, (tuple(excluded[:EXCLUDED_SHOWN]), len(excluded))


def _in_scope(index: CatalogIndex, request: RankingRequest) -> list[Interface]:
    """List the interfaces a request's scope covers.

    Args:
        index: The inventory.
        request: A ``RankingRequest``.

    Returns:
        list[Interface]: Candidates.

    Raises:
        None
    """
    return [i for i in index.interfaces if request.scope.matches(index, i)]


def utilization_rows(
    index: CatalogIndex,
    scan: RankingScan,
    request: RankingRequest,
    window: TimeWindow,
    settings: QuerySettings,
    position: str,
) -> tuple[tuple[UtilizationRow, ...], Excluded, int]:
    """Rank by the higher directional p95 utilization; ties report inbound.

    Args:
        index: The inventory.
        scan: Per-interface summaries from one collector.
        request: The ``RankingRequest`` (scope and limit).
        window: The aligned ``TimeWindow``.
        settings: Defaults.
        position: The collector's position, for exclusion words.

    Returns:
        tuple: Rows, ``(excluded, excluded total)``, and the candidate count.

    Raises:
        None
    """
    by_id = {s.interface_id: s for s in scan.items}
    window_s = window.duration.total_seconds()
    ranked: list[UtilizationRow] = []
    excluded: list[ExcludedRow] = []
    candidates = _in_scope(index, request)
    for iface in candidates:
        ref, cap, s = interface_ref(index, iface), capacity_of(iface), by_id.get(iface.id)
        if cap is None or s is None or (s.in_p95_bps is None and s.out_p95_bps is None):
            excluded.append(ExcludedRow(ref, _why_not(iface, cap is None, s, position)))
            continue
        p_in = None if s.in_p95_bps is None else 100.0 * s.in_p95_bps / cap.bps
        p_out = None if s.out_p95_bps is None else 100.0 * s.out_p95_bps / cap.bps
        inbound = p_out is None or (p_in is not None and p_in >= p_out)
        score = p_in if inbound else p_out
        valid = s.in_valid if inbound else s.out_valid
        coverage = CoverageView(valid, _expected(index, iface, window_s, settings))
        ranked.append(
            UtilizationRow(
                0,
                ref,
                score or 0.0,
                "in" if inbound else "out",
                p_in,
                p_out,
                s.in_p95_bps,
                s.out_p95_bps,
                cap.bps,
                cap.label,
                coverage,
            )
        )
    ranked.sort(key=lambda r: (-r.score_pct, ref_key(r.interface)))
    rows, shown = _finish(ranked, excluded, request.limit)
    return rows, shown, len(candidates)


def _why_not(iface: Interface, no_cap: bool, s: WindowSummary | None, position: str) -> str:
    """Say why an interface cannot rank by utilization (DASH-005, E.17 #15).

    Args:
        iface: The interface.
        no_cap: It has no utilization denominator.
        s: Its summary, if it had samples.
        position: The collector's position.

    Returns:
        str: Operator words.

    Raises:
        None
    """
    if no_cap:
        return no_capacity_reason(iface)
    if s is None:
        return f"no samples from {position} in the window"
    return NO_BASELINE


def error_rows(
    index: CatalogIndex,
    scan: RankingScan,
    request: RankingRequest,
    window: TimeWindow,
    settings: QuerySettings,
    position: str,
) -> tuple[tuple[ErrorRow, ...], Excluded, int]:
    """Rank by valid error deltas; zero-error interfaces rank at the bottom.

    Args:
        index: The inventory.
        scan: Per-interface summaries from one collector.
        request: The ``RankingRequest`` (scope and limit).
        window: The aligned ``TimeWindow``.
        settings: Defaults.
        position: The collector's position, for exclusion words.

    Returns:
        tuple: Rows, ``(excluded, excluded total)``, and the candidate count.

    Raises:
        None
    """
    by_id = {s.interface_id: s for s in scan.items}
    window_s = window.duration.total_seconds()
    ranked: list[ErrorRow] = []
    excluded: list[ExcludedRow] = []
    candidates = _in_scope(index, request)
    for iface in candidates:
        ref, s = interface_ref(index, iface), by_id.get(iface.id)
        if s is None or s.error_samples == 0:
            reason = NO_BASELINE if s else f"no samples from {position} in the window"
            excluded.append(ExcludedRow(ref, reason))
            continue
        errors = s.in_errors + s.out_errors
        coverage = CoverageView(s.error_samples, _expected(index, iface, window_s, settings))
        ranked.append(
            ErrorRow(
                0, ref, errors, s.in_errors, s.out_errors, per_million(errors, s.packets), coverage
            )
        )
    ranked.sort(
        key=lambda r: (
            -r.errors,
            r.per_million is None,
            -(r.per_million or 0.0),
            ref_key(r.interface),
        )
    )
    rows, shown = _finish(ranked, excluded, request.limit)
    return rows, shown, len(candidates)
