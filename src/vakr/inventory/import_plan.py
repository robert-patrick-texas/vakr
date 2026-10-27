"""The deterministic dry run of ``vakr inventory import`` (META-010, CFG-010, E.38).

An import is a change set of API-managed records, not a second file
source (E.38 decision 1). Against what is stored, each row:

- **creates** a site or device no stored record matches (by ``uuid`` when
  given, else by name), owned ``api:import``; a supplied UUID becomes the
  record's identity (META-001);
- **updates** an API-managed record, changing only the fields the row
  gives (a CSV column left out, a TOML key left out);
- is **unchanged** when those fields already match, whoever owns it;
- is a **conflict** when it would change a record ``inventory.toml`` owns,
  states another owner than storage, or collides by name, UUID, or address.

Nothing the file leaves out is disabled, released, or touched. Every
problem is listed at its row and column, and any one refuses the whole
import. Changes come in a fixed order (sites, devices, interfaces; each by
name), so the same file and storage always give byte-identical output, and
an applied import plans as no changes the second time (CFG-011).

Dependencies:
    vakr.inventory.import_checks, import_interfaces: Row checks; annotations.
    vakr.inventory.ownership, plan, validate: Matching, diffs, file checks.

Safety:
    Pure: the plan is computed from a snapshot.

Example:
    >>> from vakr.inventory.snapshot import EMPTY
    >>> plan_import(InventoryFile("d.csv"), EMPTY, Checks())[0].summary
    'no changes'
"""

from collections.abc import Collection
from uuid import UUID

from vakr.domain.enums import ManagedByKind
from vakr.domain.inventory import Site
from vakr.inventory.csv_codec import locate
from vakr.inventory.errors import InventoryError, Issue
from vakr.inventory.import_checks import (
    LABEL_KEY,
    Scope,
    device_facts,
    file_conflict,
    owner_problem,
    patch,
)
from vakr.inventory.import_interfaces import annotation_changes
from vakr.inventory.model import InventoryFile, SiteEntry
from vakr.inventory.ownership import locate_record
from vakr.inventory.plan import device_pairs
from vakr.inventory.plan_model import Action, Change, InventoryPlan, diff
from vakr.inventory.snapshot import Snapshot, load_snapshot
from vakr.inventory.validate import Checks, Findings, unique_problems
from vakr.ports.metadata import MetadataUnitOfWork


def _site_pairs(s: SiteEntry, site: Site) -> list[tuple[str, object, object]]:
    """Pair a stored site's fields with the row's.

    Args:
        s: The row.
        site: The stored site.

    Returns:
        list[tuple[str, object, object]]: ``(field, stored, wanted)``.

    Raises:
        None
    """
    return [("name", site.name, s.name), ("code", site.code, s.code),
            ("region", site.region, s.region), ("timezone", site.timezone, s.timezone),
            ("description", site.description, s.description)]  # fmt: skip


def _sites(scope: Scope) -> tuple[list[Change], int]:
    """Plan site rows (TOML imports only; a CSV holds devices or annotations).

    Args:
        scope: The import.

    Returns:
        tuple[list[Change], int]: Changes by name and the unchanged count.

    Raises:
        None
    """
    changes, same = [], 0
    for s in sorted(scope.imp.sites, key=lambda e: e.name):
        stored, conflict = locate_record(s, scope.snapshot.site, "site")
        if conflict:
            scope.problem(f"{s.at}.{'uuid' if s.uuid else 'name'}", conflict)
            continue
        if stored is None:
            changes.append(Change(Action.CREATE, "site", s.name, None, (f"code {s.code}",), s))
            continue
        changed = patch(_site_pairs(s, stored), s.given)
        if changed and stored.managed_by.kind is ManagedByKind.FILE:
            why = file_conflict("site", stored.id, stored.managed_by, "change it")
            scope.problem(f"{s.at}.{changed[0][0]}", why)
        elif changed:
            changes.append(Change(Action.UPDATE, "site", s.name, stored.id, diff(changed), s))
        same += not changed
    return changes, same


def _devices(scope: Scope) -> tuple[list[Change], int]:
    """Plan device rows: create, update, unchanged, or conflict.

    Args:
        scope: The import.

    Returns:
        tuple[list[Change], int]: Changes by name and the unchanged count.

    Raises:
        None
    """
    changes, same, seen = [], 0, {}
    for d in sorted(scope.imp.devices, key=lambda e: e.name):
        stored, conflict = locate_record(d, scope.snapshot.device_named, "device")
        if conflict:
            scope.problem(f"{d.at}.{'uuid' if d.uuid else 'name'}", conflict)
            continue
        scope.targets[d.name] = stored
        device_facts(scope, d, stored)
        if why := owner_problem(d, None if stored is None else stored.managed_by, "device"):
            scope.problem(f"{d.at}.managed_by", why)
        elif stored is None:
            where = d.address + (f" at site {d.site}" if d.site else "")
            changes.append(Change(Action.CREATE, "device", d.name, None, (where,), d))
        elif (other := seen.setdefault(stored.id, d.at)) != d.at:
            scope.problem(
                f"{d.at}.{'uuid' if d.uuid else 'name'}", f"names the same device as {other}"
            )
        elif changed := patch(device_pairs(d, stored), d.given):
            if stored.managed_by.kind is ManagedByKind.FILE:
                why = file_conflict("device", stored.id, stored.managed_by, "change it")
                scope.problem(f"{d.at}.{LABEL_KEY.get(changed[0][0], changed[0][0])}", why)
            else:
                changes.append(Change(Action.UPDATE, "device", d.name, stored.id, diff(changed), d))
        else:
            same += 1
    return changes, same


def plan_import(
    imp: InventoryFile, snapshot: Snapshot, checks: Checks
) -> tuple[InventoryPlan, Findings]:
    """Validate an import against storage and compute what it would change.

    Args:
        imp: The parsed import file.
        snapshot: Stored records, from ``import_snapshot``.
        checks: Credential names and target guardrails.

    Returns:
        tuple[InventoryPlan, Findings]: The plan and its warnings.

    Raises:
        InventoryError: Listing every problem at its row and column; the
            import must not be applied.
    """
    scope = Scope(imp, snapshot, checks)
    unique_problems(imp, scope.problems)
    sites, same_sites = _sites(scope)
    devices, same_devices = _devices(scope)
    interfaces, waiting, same_ifaces = annotation_changes(scope)
    if checks.credentials is None and any(d.credential for d in imp.devices):
        scope.problems.append(
            Issue("", f"credential names not checked: {checks.credentials_problem}")
        )
    if scope.problems:
        raise InventoryError(imp.source, locate(scope.problems))
    plan = InventoryPlan(imp.source, (*sites, *devices, *interfaces), waiting,
                         same_sites + same_devices + same_ifaces)  # fmt: skip
    return plan, Findings((), tuple(locate(scope.warnings)))


def _renamed(imp: InventoryFile) -> Collection[UUID]:
    """Devices a row names by UUID under a name annotation rows use.

    Args:
        imp: The import.

    Returns:
        Collection[UUID]: Their UUIDs, whose interfaces must be read too.

    Raises:
        None
    """
    annotated = {a.device for a in imp.annotations}
    return {d.uuid for d in imp.devices if d.uuid is not None and d.name in annotated}


def import_snapshot(uow: MetadataUnitOfWork, imp: InventoryFile) -> Snapshot:
    """Read what an import is planned against, in one unit of work.

    Args:
        uow: The open unit of work.
        imp: The import.

    Returns:
        Snapshot: Every site and device, and the interfaces of every device
        an annotation row names.

    Raises:
        StorageError: If the store fails or a list exceeds its bound.
    """
    return load_snapshot(uow, imp, _renamed(imp))
