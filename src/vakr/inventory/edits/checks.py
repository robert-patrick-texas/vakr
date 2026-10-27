"""Cross-record checks of a device edit: the file's checks plus API-side conflicts (CONF-008, 15.5).

The checks ``vakr inventory validate`` runs on each ``[[devices]]`` entry
run here unchanged, by validating a one-device file against the stored
sites: the target guardrails (15.5), that the site exists, and that the
credential name exists in the protected provider (names only; a device
without one is a warning unless it is ``icmp_only``). Then the conflict
rules seen from the API's side, the mirror of the file's (CONF-008):

- a name belongs to one device, retired or not (the natural key);
- an address polled by another device that is not retired, whoever owns
  it, is refused, so the same router is never polled twice under two names.

Dependencies:
    vakr.inventory.validate, snapshot: The file's checks and stored sites.

Safety:
    Pure. Messages name devices, owners, and credentials, never secrets.

Example:
    >>> from vakr.inventory.edits.fields import parse_device
    >>> entry = parse_device({"name": "edge9", "address": "192.0.2.9"})
    >>> device_findings(entry, (), (), Checks(), None)[1][0].path
    'credential'
"""

from collections.abc import Sequence

from vakr.domain.enums import AdminState
from vakr.domain.ids import DeviceId
from vakr.domain.inventory import Device, Site
from vakr.inventory.edits.ownership import owner_text
from vakr.inventory.errors import Issue
from vakr.inventory.model import DeviceEntry, InventoryFile
from vakr.inventory.snapshot import Snapshot
from vakr.inventory.validate import Checks, validate

_AT = "devices[0]."


def _field(issue: Issue) -> Issue:
    """Re-root a one-device file's problem at the edit's field.

    Args:
        issue: A problem at ``devices[0].address``, or at ``devices`` when the
            credential names could not be read.

    Returns:
        Issue: The problem at ``address`` (or ``credential``).

    Raises:
        None
    """
    path = "credential" if issue.path == "devices" else issue.path.removeprefix(_AT)
    return Issue(path, issue.reason)


def _conflicts(entry: DeviceEntry, devices: Sequence[Device], self_id: DeviceId | None
               ) -> list[Issue]:  # fmt: skip
    """Refuse a name or an active address another device already has (CONF-008).

    Args:
        entry: The device as the edit would leave it.
        devices: Every stored device, retired ones included.
        self_id: The device being changed, or ``None`` for a new one.

    Returns:
        list[Issue]: Problems by field.

    Raises:
        None
    """
    problems = []
    active = entry.admin_state is not AdminState.RETIRED
    for other in devices:
        if other.id == self_id:
            continue
        if other.name == entry.name:
            state = ", retired" if other.admin_state is AdminState.RETIRED else ""
            why = f"{entry.name!r} is already the name of device {other.id}"
            problems.append(Issue("name", f"{why} ({owner_text(other.managed_by)}{state})"))
        polled = other.admin_state is not AdminState.RETIRED
        if active and polled and other.poll_address.lower() == entry.address.lower():
            why = f"{entry.address} is already polled by device {other.name!r}"
            owner = owner_text(other.managed_by)
            problems.append(Issue("address", f"{why} ({owner}; CONF-008)"))
    return problems


def device_findings(
    entry: DeviceEntry, devices: Sequence[Device], sites: Sequence[Site], checks: Checks,
    self_id: DeviceId | None,
) -> tuple[list[Issue], list[Issue]]:  # fmt: skip
    """Run every check of one device edit.

    Args:
        entry: The device as the edit would leave it.
        devices: Every stored device, retired ones included.
        sites: Every stored site.
        checks: Credential names and target guardrails, as ``vakr inventory apply`` uses.
        self_id: The device being changed, or ``None`` for a new one.

    Returns:
        tuple[list[Issue], list[Issue]]: Problems and warnings, by field.

    Raises:
        None
    """
    found = validate(InventoryFile("edit", devices=(entry,)), Snapshot(tuple(sites)), checks)
    problems = [_field(i) for i in found.problems]
    return problems + _conflicts(entry, devices, self_id), [_field(i) for i in found.warnings]
