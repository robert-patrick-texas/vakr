"""Assemble discovered interfaces from walked IF-MIB columns (DISC-005, DISC-007).

Every ifIndex that appears in any walked column becomes one
``DiscoveredInterface``, whatever its state or policy, because suppressed
interfaces are still inventoried (DISC-007). A field is listed as missing
when it is required (``ifDescr``, ``ifType``, both statuses, ``ifName``) and
absent, or when its column walk stopped early so its absence proves
nothing. An absent value from a column walked to the end is simply not
implemented for that row, and is ``None`` without a flag.

Capability probes read three columns: a value means supported, absence from
a complete walk means unsupported, and absence from an incomplete walk means
unknown (``None``), so a failed probe never disables HC counters (SNMP-010).

Dependencies:
    vakr.discovery.mib, vakr.discovery.parse: Columns and row parsing.
    vakr.discovery.model: ``DiscoveredInterface``.

Safety:
    Pure. Values of the wrong SNMP type count as absent.

Example:
    >>> build_interfaces({}, {}, set())
    ()
"""

from collections.abc import Mapping
from dataclasses import replace

from vakr.discovery.mib import IF_COLUMNS, IFX_COLUMNS
from vakr.discovery.model import DiscoveredInterface
from vakr.discovery.parse import by_if_index, mac_text
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.quality import Quality
from vakr.ports.snmp import VarBind, WalkResult

REQUIRED = frozenset({"ifDescr", "ifType", "ifAdminStatus", "ifOperStatus", "ifName"})
"""Fields an interface must have; their absence is flagged ``MISSING_FIELDS``."""
_COLUMNS = {**IF_COLUMNS, **IFX_COLUMNS}


class _Row:
    """Reads one ifIndex's values across the walked columns.

    Attributes:
        missing: Fields recorded as missing so far.
    """

    def __init__(
        self,
        if_index: int,
        values: Mapping[str, Mapping[int, VarBind]],
        walks: Mapping[str, WalkResult],
    ) -> None:
        """Bind the row to the walked columns.

        Args:
            if_index: The row.
            values: Column name to ifIndex to varbind.
            walks: Column name to walk result.

        Returns:
            None

        Raises:
            None
        """
        self._index = if_index
        self._values = values
        self._walks = walks
        self.missing: list[str] = []

    def _complete(self, name: str) -> bool:
        """Report whether a column was walked to its end.

        Args:
            name: Column name.

        Returns:
            bool: True for a complete walk.

        Raises:
            None
        """
        walk = self._walks.get(name)
        return walk is not None and walk.complete

    def _varbind(self, name: str) -> VarBind | None:
        """Return the row's value in one column, if the agent sent one.

        Args:
            name: Column name.

        Returns:
            VarBind | None: The varbind, or ``None`` when absent.

        Raises:
            None
        """
        vb = self._values.get(name, {}).get(self._index)
        return vb if vb is not None and vb.present else None

    def _seen[T](self, name: str, value: T | None) -> T | None:
        """Record ``name`` as missing when its absence matters.

        Args:
            name: Column name.
            value: The converted value, or ``None``.

        Returns:
            T | None: ``value``, unchanged.

        Raises:
            None
        """
        if value is None and (name in REQUIRED or not self._complete(name)):
            self.missing.append(name)
        return value

    def text(self, name: str) -> str | None:
        """Return a DisplayString field.

        Args:
            name: Column name.

        Returns:
            str | None: The text, or ``None``.

        Raises:
            None
        """
        vb = self._varbind(name)
        return self._seen(name, None if vb is None else vb.text())

    def integer(self, name: str) -> int | None:
        """Return a numeric field.

        Args:
            name: Column name.

        Returns:
            int | None: The number, or ``None``.

        Raises:
            None
        """
        vb = self._varbind(name)
        return self._seen(name, None if vb is None else vb.integer())

    def status[T: (IfAdminStatus, IfOperStatus)](self, name: str, kind: type[T]) -> T | None:
        """Return an IF-MIB status, or ``None`` for a value the MIB does not define.

        Args:
            name: ``ifAdminStatus`` or ``ifOperStatus``.
            kind: The matching enum.

        Returns:
            T | None: The status.

        Raises:
            None
        """
        value = self.integer(name)
        if value is not None and value not in kind._value2member_map_:
            self.missing.append(name)
            return None
        return None if value is None else kind(value)

    def mac(self) -> str | None:
        """Return ``ifPhysAddress``; an empty one means no MAC, not missing data.

        Args:
            None

        Returns:
            str | None: Lower-case colon hex, or ``None``.

        Raises:
            None
        """
        vb = self._varbind("ifPhysAddress")
        if vb is None:
            self._seen("ifPhysAddress", None)
            return None
        return mac_text(vb.octets())

    def probe(self, name: str) -> bool | None:
        """Return a capability: supported, unsupported, or unknown.

        Args:
            name: Probe column name.

        Returns:
            bool | None: True if the row has a value, False if a complete
            walk lacks it, ``None`` if the walk stopped early.

        Raises:
            None
        """
        if self._varbind(name) is not None:
            return True
        return False if self._complete(name) else None


def build_interfaces(
    walks: Mapping[str, WalkResult], addresses: Mapping[int, list[str]], unknown_prefix: set[int]
) -> tuple[DiscoveredInterface, ...]:
    """Build one ``DiscoveredInterface`` per ifIndex seen in any column.

    Args:
        walks: IF-MIB and ifXTable walks by column name.
        addresses: Interface addresses by ifIndex.
        unknown_prefix: ifIndexes with an address whose prefix is unknown.

    Returns:
        tuple[DiscoveredInterface, ...]: In ifIndex order.

    Raises:
        None
    """
    values = {
        name: by_if_index(walk, _COLUMNS[name]) for name, walk in walks.items() if name in _COLUMNS
    }
    indexes = sorted({i for column in values.values() for i in column})
    return tuple(_interface(i, values, walks, addresses, unknown_prefix) for i in indexes)


def _interface(
    if_index: int,
    values: Mapping[str, Mapping[int, VarBind]],
    walks: Mapping[str, WalkResult],
    addresses: Mapping[int, list[str]],
    unknown_prefix: set[int],
) -> DiscoveredInterface:
    """Build one interface.

    Args:
        if_index: The row.
        values: Column name to ifIndex to varbind.
        walks: Column name to walk result.
        addresses: Interface addresses by ifIndex.
        unknown_prefix: ifIndexes with an unknown address prefix.

    Returns:
        DiscoveredInterface: The interface, with missing fields flagged.

    Raises:
        None
    """
    row = _Row(if_index, values, walks)
    interface = DiscoveredInterface(
        if_index=if_index,
        if_descr=row.text("ifDescr"),
        if_name=row.text("ifName"),
        if_alias=row.text("ifAlias"),
        if_type=row.integer("ifType"),
        if_mtu=row.integer("ifMtu"),
        if_speed=row.integer("ifSpeed"),
        if_high_speed=row.integer("ifHighSpeed"),
        mac=row.mac(),
        admin_status=row.status("ifAdminStatus", IfAdminStatus),
        oper_status=row.status("ifOperStatus", IfOperStatus),
        last_change_ticks=row.integer("ifLastChange"),
        addresses=tuple(dict.fromkeys(addresses.get(if_index, ()))),
        hc_octets=row.probe("ifHCInOctets"),
        hc_packets=row.probe("ifHCInUcastPkts"),
        discontinuity=row.probe("ifCounterDiscontinuityTime"),
    )
    if if_index in unknown_prefix:
        row.missing.append("address_prefix")
    if not row.missing:
        return interface
    return replace(interface, missing=tuple(row.missing), quality=Quality.MISSING_FIELDS)
