"""View models of the device screen: the interface table and the device detail (TUI-004..006).

Split from ``vakr.query.views`` to keep each file short; the same rules
apply: every value is a ``Reading``, and a missing value is "—" with a
reason, never zero (invariant 1).

Dependencies:
    vakr.query.views: ``Reading``, ``DeviceRow``, and ``HealthView``.
    vakr.domain: IDs and lifecycle vocabulary.

Safety:
    Views never carry credential references or secrets (TUI-010).

Example:
    >>> InterfaceRow.__name__, DeviceDetailView.__name__
    ('InterfaceRow', 'DeviceDetailView')
"""

from dataclasses import dataclass
from datetime import datetime

from vakr.domain.enums import InterfaceLifecycle
from vakr.domain.ids import InterfaceId
from vakr.query.views import DeviceRow, HealthView, Reading


@dataclass(frozen=True, slots=True)
class InterfaceRow:
    """One line of a device's interface table (TUI-005, 12.5).

    Attributes:
        interface_id: Persistent interface UUID (never ``ifIndex``).
        name: ``ifName``, or ``ifDescr`` when the agent reports no name.
        alias: ``ifAlias``, the description configured on the device.
        markers: ``sticky``, ``WAN``, ``primary`` flags that order the table.
        lifecycle: Inventory lifecycle state.
        state: ``admin/oper`` text, such as ``up/down``.
        in_rate: Inbound bits per second.
        out_rate: Outbound bits per second.
        in_util: Inbound utilization of the effective capacity.
        out_util: Outbound utilization of the effective capacity.
        in_pps: Inbound packets per second.
        out_pps: Outbound packets per second.
        errors: Input and output error count since the previous sample.
        capacity: Effective capacity and its source (RATE-008).
        age: Age of the sample.
    """

    interface_id: InterfaceId
    name: str
    alias: str
    markers: tuple[str, ...]
    lifecycle: InterfaceLifecycle
    state: Reading
    in_rate: Reading
    out_rate: Reading
    in_util: Reading
    out_util: Reading
    in_pps: Reading
    out_pps: Reading
    errors: Reading
    capacity: Reading
    age: Reading


@dataclass(frozen=True, slots=True)
class DeviceDetailView:
    """Everything the device screen and ``vakr status DEVICE`` show (TUI-004..006, 12.4).

    Attributes:
        row: The device's list row (status, reachability, SNMP, uptime, ...).
        facts: Labelled summary values in display order.
        warnings: Quality conditions on the newest evidence, in operator words.
        interfaces: Interfaces in TUI-006 order (sticky, WAN, primary first).
        interfaces_total: Interfaces of the device in inventory.
        interfaces_truncated: True when more interfaces exist than one view reads.
        generated_at: When the view was computed (UTC).
        health: Storage and collector health.
        notes: Standing explanations, such as what ``sysUpTime`` measures.
    """

    row: DeviceRow
    facts: tuple[tuple[str, Reading], ...]
    warnings: tuple[str, ...]
    interfaces: tuple[InterfaceRow, ...]
    interfaces_total: int
    interfaces_truncated: bool
    generated_at: datetime
    health: HealthView
    notes: tuple[str, ...] = ()
