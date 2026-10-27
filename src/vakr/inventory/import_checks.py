"""Row checks shared by the import plan: ownership, patches, and device facts (E.38, CONF-008).

An import is a bulk write of API-managed records (E.38 decision 1), so the
ownership rule is the mirror of ``inventory.toml``'s: a row may create a
record or change an API-managed one, and a row that would change a record
``inventory.toml`` owns is a conflict naming the owner and the fix. A row
that matches such a record exactly is fine, which is what lets an export of
everything plan as "no changes". Ownership itself never moves: a
``managed_by`` value that disagrees with storage is refused.

Rows change only the fields they give (``given``), so every comparison
here is a patch: pairs whose field the row wrote and whose values differ.

Dependencies:
    vakr.inventory.guardrails, validate: Target policy and checks.
    vakr.domain.addressing: Poll address rules.

Safety:
    Pure. Messages name credentials, never secrets.

Example:
    >>> patch([("name", "a", "b"), ("comment", "x", "x")], {"name", "comment"})
    [('name', 'a', 'b')]
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from uuid import UUID

from vakr.domain.addressing import require_poll_address
from vakr.domain.enums import AdminState, ManagedByKind, ReachabilityStrategy
from vakr.domain.errors import ValidationError
from vakr.domain.ownership import ManagedBy
from vakr.inventory.errors import Issue
from vakr.inventory.guardrails import target_problem
from vakr.inventory.model import (
    IMPORT_OWNER,
    AnnotationEntry,
    DeviceEntry,
    InventoryFile,
    SiteEntry,
)
from vakr.inventory.snapshot import Snapshot, StoredDevice
from vakr.inventory.validate import Checks

LABEL_KEY = {
    "interval_s": "interval", "discovery_interval_s": "discovery_interval",
    "is_wan_circuit": "wan_circuit", "public_dashboard": "public_dashboard_eligible",
}  # fmt: skip
"""The file key (CSV column) of each diff label whose name differs."""

MISSING = object()
"""``Scope.device_for``: no device by that name in storage or the import."""


@dataclass(slots=True)
class Scope:
    """One import being planned.

    Attributes:
        imp: The import file.
        snapshot: Stored records.
        checks: Credential names and target guardrails.
        problems: Errors found so far; any one refuses the whole import.
        warnings: Advice that does not block.
        targets: Device rows by name: the stored device each changes, or
            ``None`` for one it creates.
        polled: Stored devices that are not retired, by lower-cased address.
    """

    imp: InventoryFile
    snapshot: Snapshot
    checks: Checks
    problems: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    targets: dict[str, StoredDevice | None] = field(default_factory=dict)
    polled: dict[str, StoredDevice] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Index stored devices that are not retired by poll address.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.polled = {d.device.poll_address.lower(): d for d in self.snapshot.devices
                       if d.device.admin_state is not AdminState.RETIRED}  # fmt: skip

    def problem(self, at: str, reason: str) -> None:
        """Record an error.

        Args:
            at: Entry path and key, such as ``row 4.address``.
            reason: What is wrong.

        Returns:
            None

        Raises:
            None
        """
        self.problems.append(Issue(at, reason))

    def device_for(self, name: str) -> StoredDevice | object | None:
        """Find the device an annotation row names, preferring this import's device rows.

        Args:
            name: Device name as the row writes it.

        Returns:
            StoredDevice | None | object: The device; ``None`` when this
            import creates it; ``MISSING`` when there is none.

        Raises:
            None
        """
        if name in self.targets:
            return self.targets[name]
        found = self.snapshot.device_named(name)
        return MISSING if found is None else found


def patch(
    pairs: list[tuple[str, object, object]], given: Collection[str]
) -> list[tuple[str, object, object]]:
    """Keep the pairs whose field the row wrote and whose values differ.

    Args:
        pairs: ``(field, stored, wanted)`` triples.
        given: Field labels the row wrote.

    Returns:
        list[tuple[str, object, object]]: The changes, in order.

    Raises:
        None
    """
    return [p for p in pairs if p[0] in given and p[1] != p[2]]


def file_conflict(what: str, record_id: UUID, owner: ManagedBy, fix: str) -> str:
    """Explain why an import may not change a file-managed record (CONF-008).

    Args:
        what: ``site``, ``device``, or ``interface``.
        record_id: The record.
        owner: Its owner.
        fix: What to change instead.

    Returns:
        str: The message.

    Raises:
        None
    """
    return f"{what} {record_id} is {owner}; {fix} in {owner.source}, not by import (CONF-008)"


def owner_problem(
    entry: SiteEntry | DeviceEntry | AnnotationEntry, owner: ManagedBy | None, what: str
) -> str | None:
    """Check a row's ``managed_by`` against storage; an import never moves ownership.

    Args:
        entry: The row.
        owner: The stored record's owner, or ``None`` when the row creates it.
        what: ``site``, ``device``, or ``interface``.

    Returns:
        str | None: The problem, or ``None``.

    Raises:
        None
    """
    said = entry.managed_by
    if said is None or said == owner:
        return None
    if owner is None and said.kind is ManagedByKind.API:
        return None  # a create: the record will be IMPORT_OWNER, an API owner like the one stated
    if owner is None:
        return (f"an import creates API-managed records ({IMPORT_OWNER}); to manage this {what} "
                f"from {said.source}, add it there")  # fmt: skip
    return f"the stored {what} is {owner}; an import never changes ownership (E.38)"


def _address(scope: Scope, d: DeviceEntry, stored: StoredDevice | None) -> None:
    """Check the address a row writes: present, allowed, unique, and of its family.

    Args:
        scope: The import.
        d: The row.
        stored: The device it changes, if any.

    Returns:
        None

    Raises:
        None
    """
    if "address" in d.given and not d.address:
        scope.problem(f"{d.at}.address", "cannot be empty; every device needs a poll address")
    if stored is None and not d.address:
        scope.problem(f"{d.at}.address", "required to create a device")
    if d.address and (why := target_problem(d.address, scope.checks.targets)) is not None:
        scope.problem(f"{d.at}.address", f"{d.address} {why} (15.5)")
    other = scope.polled.get(d.address.lower()) if d.address else None
    if other is not None and (stored is None or other.id != stored.id):
        scope.problem(f"{d.at}.address", f"already polled as {other.managed_by} device "
                                         f"{other.device.name!r} (CONF-008)")  # fmt: skip
    if stored is not None and d.address and {"address", "address_family"} & d.given:
        address = d.address if "address" in d.given else stored.device.poll_address
        family = d.address_family if "address_family" in d.given else stored.device.address_family
        try:
            require_poll_address(address, family)
        except ValidationError as exc:
            scope.problem(f"{d.at}.address_family", str(exc).removeprefix("poll_address "))


def device_facts(scope: Scope, d: DeviceEntry, stored: StoredDevice | None) -> None:
    """Check what a device row refers to: its address, site, and credential.

    Args:
        scope: The import.
        d: The row.
        stored: The device it changes, or ``None`` for a create.

    Returns:
        None

    Raises:
        None
    """
    _address(scope, d, stored)
    sites = {s.name for s in scope.imp.sites} | {s.name for s in scope.snapshot.sites}
    if "site" in d.given and d.site is not None and d.site not in sites:
        scope.problem(f"{d.at}.site", f"no site {d.site!r} in storage or this import")
    names = scope.checks.credentials
    if "credential" in d.given and d.credential and names is not None and d.credential not in names:
        why = f"no SNMP credential named {d.credential!r} in the credentials file"
        scope.problem(f"{d.at}.credential", why)
    if (
        stored is None
        and d.credential is None
        and d.reachability is not ReachabilityStrategy.ICMP_ONLY
    ):
        why = 'not set; SNMP polling will fail unless reachability = "icmp_only"'
        scope.warnings.append(Issue(f"{d.at}.credential", why))
