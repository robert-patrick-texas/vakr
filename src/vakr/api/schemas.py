"""Response shapes of API v1 that the query views do not already define (API-001, API-002).

Most endpoints return a ``vakr.query`` view as it is (through
``to_jsonable``). The shapes here cover the rest: version and health,
the signed-in session, catalog records, identity history and reviews,
and the paged wrappers that add ``page`` (``vakr.api.paging``) to the
device and interface list views. They are explicit allowlists: a field
added to a domain record does not appear in the API until it is added
here, so the v1 contract changes only on purpose (API-004).

Every identifier is a UUID (API-002); every time is UTC with ``Z``.

Dependencies:
    dataclasses: The shapes; FastAPI derives the OpenAPI schemas from them.
    vakr.query: Views reused inside these shapes.

Safety:
    No shape has a field for a password, hash, token, community, or
    credential reference.

Example:
    >>> SessionInfo("local", "local:root", "admin").csrf_token is None
    True
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from vakr.api.paging import PageInfo
from vakr.query import DeviceListView
from vakr.query.catalog_views import InterfaceListRow, InterfaceListView
from vakr.query.views import CollectorHealthView, Reading


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """What is running.

    Attributes:
        version: The vakr release.
        api: The API major version, ``v1`` (API-004).
    """

    version: str
    api: str = "v1"


@dataclass(frozen=True, slots=True)
class CheckInfo:
    """One readiness or health component.

    Attributes:
        name: ``metadata``, ``telemetry``, ``auth``, or a collector position.
        status: ``ok``, ``degraded``, or ``unavailable``.
        detail: Operator words; only for authenticated callers.
    """

    name: str
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HealthInfo:
    """``health/live``, ``health/ready``, and ``health/detail`` (SVC-004).

    Attributes:
        status: ``ok``, ``ready``, ``degraded``, or ``not_ready``.
        checks: Components; names and states only for anonymous callers.
        version: The release (``health/detail`` only).
        started_at: When the process became ready (``health/detail`` only).
        collectors: Each collector's latest self-report (``health/detail`` only).
    """

    status: str
    checks: tuple[CheckInfo, ...] = ()
    version: str | None = None
    started_at: datetime | None = None
    collectors: tuple[CollectorHealthView, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Who the caller is (``GET /api/v1/auth/session`` and the login answer).

    Attributes:
        kind: ``user`` (session cookie), ``kiosk`` (kiosk cookie or bearer token), or
            ``local`` (socket).
        name: Username, ``kiosk:<label>``, or ``local:<account>``.
        role: ``admin`` or ``operator``.
        csrf_token: Send as ``X-CSRF-Token`` on every state-changing request (users only).
        expires_at: Absolute expiry of the session, or of a kiosk display's token.
        idle_timeout_s: The session ends after this long without a request (users only).
        user_id: The account (users only).
    """

    kind: str
    name: str
    role: str
    csrf_token: str | None = None
    expires_at: datetime | None = None
    idle_timeout_s: int | None = None
    user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SiteOut:
    """A site (META-006).

    Attributes:
        id: Site UUID.
        name: Display name.
        code: Short code.
        region: Region label.
        timezone: IANA zone for local displays.
        description: Free text.
    """

    id: UUID
    name: str
    code: str
    region: str | None
    timezone: str | None
    description: str


@dataclass(frozen=True, slots=True)
class LabelOut:
    """A tag or a group (META-005).

    Attributes:
        id: UUID.
        name: Display name.
        description: Free text.
    """

    id: UUID
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class CollectorOut:
    """A collector (polling position) with its latest health (MPOL-006, OBS-002).

    Attributes:
        id: Collector UUID; every observation carries it.
        name: ``scheduler.collector_name``.
        position: Position label, such as ``east``.
        enabled: Whether it is an active position.
        health: Its latest self-report, or ``None`` when it never reported.
    """

    id: UUID
    name: str
    position: str
    enabled: bool
    health: CollectorHealthView | None


@dataclass(frozen=True, slots=True)
class SitePage:
    """A page of sites.

    Attributes:
        items: The sites, in name order.
        page: Paging facts.
    """

    items: tuple[SiteOut, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class LabelPage:
    """A page of tags or groups.

    Attributes:
        items: The labels, in name order.
        page: Paging facts.
    """

    items: tuple[LabelOut, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class CollectorPage:
    """A page of collectors.

    Attributes:
        items: The collectors, in name order.
        page: Paging facts.
    """

    items: tuple[CollectorOut, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class DeviceListPage(DeviceListView):
    """The device list view plus paging facts (API-005).

    Attributes:
        page: Paging facts; ``next_cursor`` continues the same order.
    """

    page: PageInfo


@dataclass(frozen=True, slots=True)
class InterfaceListPage(InterfaceListView):
    """The interface list view plus paging facts (API-005).

    Attributes:
        page: Paging facts; ``next_cursor`` continues the same order.
    """

    page: PageInfo


@dataclass(frozen=True, slots=True)
class InterfaceOut:
    """An interface's identity and operator metadata (12.5, META-011).

    Attributes:
        id: Persistent interface UUID, independent of ``ifIndex``.
        device_id: Its device.
        if_index: Current ``ifIndex``, or ``None`` when not seen.
        if_name: ``ifName``.
        if_descr: ``ifDescr``.
        if_alias: ``ifAlias``.
        if_type: IANAifType number.
        mac: ``ifPhysAddress``.
        if_speed: ``ifSpeed`` in bit/s.
        if_high_speed: ``ifHighSpeed`` in Mbit/s.
        addresses: IP interface addresses.
        lifecycle: Lifecycle state (LIFE-001).
        managed_by: ``file`` or ``api`` (META-008).
        interest: ``primary`` (high priority) or ``secondary``.
        sticky: Pinned to the top of lists.
        is_wan_circuit: Marked as a WAN circuit.
        provider: Circuit provider.
        circuit_id: Carrier circuit ID.
        contracted_bps: Contracted rate.
        capacity_override_bps: Operator capacity override.
        display_rank: Ordering hint, lower first.
        comment: Operator comment.
        created_at: When the identity was created.
        updated_at: When the record last changed.
    """

    id: UUID
    device_id: UUID
    if_index: int | None
    if_name: str
    if_descr: str
    if_alias: str
    if_type: int | None
    mac: str | None
    if_speed: int | None
    if_high_speed: int | None
    addresses: tuple[str, ...]
    lifecycle: str
    managed_by: str
    interest: str
    sticky: bool
    is_wan_circuit: bool
    provider: str | None
    circuit_id: str | None
    contracted_bps: int | None
    capacity_override_bps: int | None
    display_rank: int | None
    comment: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InterfaceDetail:
    """``GET /api/v1/interfaces/{uuid}``: identity plus current values from one position.

    Attributes:
        interface: Identity and metadata.
        current: The list row with current values, or ``None`` when not found in the index.
        position: Position whose values are shown.
        telemetry: ``ok``, or why current values are unavailable.
        generated_at: When the answer was computed.
    """

    interface: InterfaceOut
    current: InterfaceListRow | None
    position: str
    telemetry: Reading
    generated_at: datetime
