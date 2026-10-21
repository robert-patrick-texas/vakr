"""``OverviewQueries``: the overview dashboard and device browsing by group (12.2, UI-008, UI-009).

The overview reuses the status machinery of ``StatusQueries`` (E.2 per
position, MPOS-007 composite, collector/spool/database health), so the web
overview, ``vakr status``, and the TUI agree about every device. It adds
the 12.2 widgets from the catalog and ranking services, and the event
feeds the stores can support today:

- restarts: management-agent restarts (``sysUpTime`` reset, UPTIME-003)
  detected within ``status.recent_restart_window``, per position;
- identity reviews: open reviews (IFID-004), newest first;
- telemetry loss: spool incidents (STORE-007) from the injected reader,
  within the last 24 hours; "not available" without one;
- rediscovery: no store records discovery runs yet, so the feed says
  "not available" rather than showing an empty list.

``browse`` is the device list filtered by site, tag, group, provider (any
interface with that provider), or admin state (UI-009).

Dependencies:
    vakr.query.service and the M4 query services; vakr.ingest.incidents (type only).

Safety:
    Read-only and bounded (E.28, E.29). Synchronous: async callers use
    ``asyncio.to_thread``.

Example:
    >>> from vakr.adapters.memory import memory_telemetry_factory, memory_uow_factory
    >>> view = OverviewQueries(memory_uow_factory(), memory_telemetry_factory()).overview()
    >>> view.devices.total, [f.available for f in view.events]
    (0, [True, False, True, False])
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from vakr.domain.enums import AdminState
from vakr.domain.ids import GroupId, SiteId, TagId
from vakr.domain.inventory import Device
from vakr.ingest.incidents import LossIncident
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory
from vakr.ports.telemetry import TelemetryBackend
from vakr.query.catalog import CatalogQueries
from vakr.query.catalog_index import IndexCache
from vakr.query.history_limits import OVERVIEW_ROWS, RankingWindow
from vakr.query.listing import DeviceListRequest, bucket_counts, matches, ordered
from vakr.query.overview_events import events
from vakr.query.overview_parts import filter_options, summary
from vakr.query.overview_views import OverviewView
from vakr.query.ranking_views import RankingRequest
from vakr.query.rankings import RankingQueries
from vakr.query.service import StatusQueries
from vakr.query.settings import QuerySettings
from vakr.query.snapshot import Inventory, TelemetrySnapshot, read_inventory, read_telemetry
from vakr.query.views import DeviceListView, DeviceRow

type IncidentReader = Callable[[], Sequence[LossIncident]]


@dataclass(frozen=True, slots=True)
class DeviceScope:
    """Which devices to browse (UI-009); ``None`` fields do not filter.

    Attributes:
        site_id: Only devices at this site.
        tag_id: Only devices with this tag.
        group_id: Only devices in this group.
        provider: Only devices with an interface of this provider (case-insensitive).
        admin_state: Only devices in this admin state.
    """

    site_id: SiteId | None = None
    tag_id: TagId | None = None
    group_id: GroupId | None = None
    provider: str | None = None
    admin_state: AdminState | None = None

    def matches(self, device: Device, providers: set[str]) -> bool:
        """Tell whether a device is in scope.

        Args:
            device: The device.
            providers: Case-folded provider names on the device's interfaces.

        Returns:
            bool: True when every set filter matches.

        Raises:
            None
        """
        return (
            (self.site_id is None or device.site_id == self.site_id)
            and (self.tag_id is None or self.tag_id in device.tags)
            and (self.group_id is None or self.group_id in device.groups)
            and (self.provider is None or self.provider.casefold() in providers)
            and (self.admin_state is None or device.admin_state is self.admin_state)
        )


@dataclass(frozen=True, slots=True)
class _Judged:
    """Every device judged once, with what was read to judge them.

    Attributes:
        rows: Device rows in inventory order.
        view: An empty list view carrying counts, health, and truncation.
        inv: The inventory read.
        snap: The telemetry snapshot read.
    """

    rows: list[DeviceRow]
    view: DeviceListView
    inv: Inventory
    snap: TelemetrySnapshot


class OverviewQueries(StatusQueries):
    """The overview and grouped device browsing, on top of ``StatusQueries``."""

    def __init__(
        self,
        metadata: MetadataUnitOfWorkFactory,
        telemetry: Callable[[], TelemetryBackend] | None,
        settings: QuerySettings | None = None,
        clock: Clock | None = None,
        *,
        index: IndexCache | None = None,
        incidents: IncidentReader | None = None,
    ) -> None:
        """Bind the service and the M4 services it composes.

        Args:
            metadata: Opens a metadata unit of work.
            telemetry: Opens a telemetry view; ``None`` when no store exists yet.
            settings: Defaults and bounds.
            clock: Time source.
            index: Shared inventory index; a private one when ``None``.
            incidents: Reads recorded telemetry-loss incidents; ``None`` when unavailable.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(metadata, telemetry, settings, clock)
        self._index = index or IndexCache(metadata, self._settings)
        self._incidents = incidents
        self.rankings = RankingQueries(
            metadata, telemetry, self._settings, self._clock, self._index
        )
        self.catalog = CatalogQueries(metadata, telemetry, self._settings, self._clock, self._index)

    def _judge(self, include_retired: bool = False) -> _Judged:
        """Judge every device once, with the health and truncation around the list.

        Args:
            include_retired: Also judge retired devices.

        Returns:
            _Judged: Rows, an empty list view carrying health, inventory, and snapshot.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        now = self._clock.now_utc()
        with self._metadata() as uow:
            inv = read_inventory(uow, self._settings, include_retired=include_retired, count=True)
        snap = read_telemetry(self._telemetry, inv.collectors, now, self._settings)
        rows = [self._row(d, inv, snap, now)[0] for d in inv.devices]
        view = DeviceListView(
            (),
            len(rows),
            0,
            1,
            bucket_counts(rows),
            inv.truncated,
            now,
            self._health(inv, snap, now),
        )
        return _Judged(rows, view, inv, snap)

    def overview(self, window: RankingWindow = RankingWindow.M15) -> OverviewView:
        """Return the overview dashboard (12.2).

        Args:
            window: Window of the top-utilization and top-error widgets (15 minutes by default).

        Returns:
            OverviewView: Every widget.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        judged = self._judge()
        view = judged.view
        index = self._index.get()
        ranking = RankingRequest(window=window)
        return OverviewView(
            devices=summary(judged.rows, view.truncated),
            health=view.health,
            high_priority=self.catalog.high_priority(OVERVIEW_ROWS),
            wan=self.catalog.wan_circuits(limit=OVERVIEW_ROWS),
            top_utilization=self.rankings.top_utilization(ranking),
            top_errors=self.rankings.top_errors(ranking),
            events=events(
                self._metadata,
                judged.inv,
                judged.snap,
                self._incidents,
                view.generated_at,
                self._settings.restart_window_s,
            ),
            filters=filter_options(index),
            generated_at=view.generated_at,
        )

    def browse(
        self, scope: DeviceScope, request: DeviceListRequest | None = None
    ) -> DeviceListView:
        """Return the device list filtered by site, tag, group, provider, or admin state (UI-009).

        Args:
            scope: The grouping filter.
            request: Search, buckets, sort, and page, as for ``device_list``.

        Returns:
            DeviceListView: The filtered, ordered page with counts and health.

        Raises:
            StorageError: If the metadata store cannot be read.
        """
        request = request or DeviceListRequest()
        include = request.include_retired or scope.admin_state is AdminState.RETIRED
        judged = self._judge(include_retired=include)
        index = self._index.get()
        providers: dict[object, set[str]] = {}
        for i in index.interfaces:
            if i.annotation.provider:
                providers.setdefault(i.device_id, set()).add(i.annotation.provider.casefold())
        devices = {d.id: d for d in judged.inv.devices}
        chosen = [
            r
            for r in judged.rows
            if matches(r, request)
            and scope.matches(devices[r.device_id], providers.get(r.device_id, set()))
        ]
        listed = ordered(chosen, request.sort)
        page = listed[request.offset : request.offset + request.limit]
        return replace(
            judged.view,
            rows=tuple(page),
            total=len(listed),
            offset=request.offset,
            limit=request.limit,
            counts=bucket_counts(listed),
        )
