"""Cross-record checks of ``inventory.toml`` against itself, storage, and policy (11.4, CONF-008).

Parsing proves each table is well formed; this module proves the file
makes sense as a whole:

- no two sites or devices share a name or UUID, no two devices share an
  address, and no interface is annotated twice;
- every site a device names exists in the file or in storage, every
  annotated device is declared in the file, and every credential name
  exists in the protected provider (names only; no secret is read here);
- every IP literal passes the target guardrails (15.5);
- no entry collides with an API-managed record by name, UUID, or address.
  File and API ownership never resolve by last writer (CONF-008): the
  operator removes the entry, or removes or retires the API record first.

Problems fail validation; warnings (a device without a credential) are
printed and do not.

Dependencies:
    vakr.inventory.guardrails, snapshot: Target policy and stored records.

Safety:
    Pure. Messages name credentials, never their secrets.

Example:
    >>> from vakr.inventory.snapshot import EMPTY
    >>> from vakr.inventory.model import InventoryFile
    >>> validate(InventoryFile("inventory.toml"), EMPTY, Checks()).problems
    ()
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from vakr.domain.enums import ReachabilityStrategy
from vakr.inventory.errors import Issue
from vakr.inventory.guardrails import TargetPolicy, target_problem
from vakr.inventory.model import InventoryFile
from vakr.inventory.ownership import ownership_problems
from vakr.inventory.snapshot import Snapshot


@dataclass(frozen=True, slots=True)
class Checks:
    """Facts from outside the file that validation needs.

    Attributes:
        credentials: SNMP credential names the provider defines; ``None``
            when the provider could not be read.
        credentials_problem: Why the provider could not be read.
        targets: The target guardrails (15.5).
    """

    credentials: frozenset[str] | None = frozenset()
    credentials_problem: str | None = None
    targets: TargetPolicy = field(default_factory=TargetPolicy)


@dataclass(frozen=True, slots=True)
class Findings:
    """The outcome of validation.

    Attributes:
        problems: Errors; the file must not be applied while any exist.
        warnings: Advice that does not block applying.
    """

    problems: tuple[Issue, ...]
    warnings: tuple[Issue, ...] = ()


def _duplicates[T](
    items: Iterable[T], key: Callable[[T], object], at: Callable[[T], str], what: str,
    problems: list[Issue],
) -> None:  # fmt: skip
    """Report every entry whose key an earlier entry already used.

    Args:
        items: Entries in file order.
        key: The value that must be unique; ``None`` is skipped.
        at: The entry's path, including the field.
        what: What is duplicated, for the message.
        problems: Problems are appended here.

    Returns:
        None

    Raises:
        None
    """
    seen: dict[object, str] = {}
    for item in items:
        value = key(item)
        if value is None:
            continue
        if value in seen:
            problems.append(Issue(at(item), f"{what} already used by {seen[value]}"))
        else:
            seen[value] = at(item).rsplit(".", 1)[0]


def unique_problems(inv: InventoryFile, problems: list[Issue]) -> None:
    """Check names, UUIDs, addresses, and annotation targets are unique in the file.

    Args:
        inv: The file.
        problems: Problems are appended here.

    Returns:
        None

    Raises:
        None
    """
    _duplicates(inv.sites, lambda s: s.name, lambda s: f"{s.at}.name", "site name", problems)
    _duplicates(inv.sites, lambda s: s.uuid, lambda s: f"{s.at}.uuid", "uuid", problems)
    _duplicates(inv.devices, lambda d: d.name, lambda d: f"{d.at}.name", "device name", problems)
    _duplicates(inv.devices, lambda d: d.uuid, lambda d: f"{d.at}.uuid", "uuid", problems)
    # An import row may omit the address (E.38); an empty one is not a duplicate.
    _duplicates(inv.devices, lambda d: d.address.lower() or None, lambda d: f"{d.at}.address",
                "address", problems)  # fmt: skip
    _duplicates(inv.annotations, lambda a: (a.device, a.ifname), lambda a: f"{a.at}.ifname",
                "this device and ifname", problems)  # fmt: skip
    _duplicates(inv.annotations, lambda a: a.uuid, lambda a: f"{a.at}.uuid", "uuid", problems)


def _devices(
    inv: InventoryFile, snapshot: Snapshot, checks: Checks
) -> tuple[list[Issue], list[Issue]]:
    """Check device sites and credentials exist.

    Args:
        inv: The file.
        snapshot: Stored records.
        checks: Credential names.

    Returns:
        tuple[list[Issue], list[Issue]]: Problems and warnings.

    Raises:
        None
    """
    problems: list[Issue] = []
    warnings: list[Issue] = []
    sites = {s.name for s in inv.sites} | {s.name for s in snapshot.sites}
    names = checks.credentials
    for d in inv.devices:
        if d.site is not None and d.site not in sites:
            problems.append(Issue(f"{d.at}.site", f"no site {d.site!r} in the file or storage"))
        if d.credential is None and d.reachability is not ReachabilityStrategy.ICMP_ONLY:
            why = 'not set; SNMP polling will fail unless reachability = "icmp_only"'
            warnings.append(Issue(f"{d.at}.credential", why))
        elif d.credential is not None and names is not None and d.credential not in names:
            why = f"no SNMP credential named {d.credential!r} in the credentials file"
            problems.append(Issue(f"{d.at}.credential", why))
    if names is None and any(d.credential for d in inv.devices):
        why = f"credential names not checked: {checks.credentials_problem}"
        problems.append(Issue("devices", why))
    return problems, warnings


def _annotated(inv: InventoryFile) -> list[Issue]:
    """Check every annotated device is declared in the file.

    Args:
        inv: The file.

    Returns:
        list[Issue]: Problems.

    Raises:
        None
    """
    declared = {d.name for d in inv.devices}
    return [
        Issue(f"{a.at}.device", f"no [[devices]] entry named {a.device!r}")
        for a in inv.annotations
        if a.device not in declared
    ]


def validate(inv: InventoryFile, snapshot: Snapshot, checks: Checks) -> Findings:
    """Run every cross-record check.

    Args:
        inv: The parsed file.
        snapshot: Stored records (``EMPTY`` before the first apply).
        checks: Credential names and target guardrails.

    Returns:
        Findings: Problems and warnings, each in file order within its check.

    Raises:
        None
    """
    problems: list[Issue] = []
    unique_problems(inv, problems)
    for d in inv.devices:
        if (why := target_problem(d.address, checks.targets)) is not None:
            problems.append(Issue(f"{d.at}.address", f"{d.address} {why} (15.5)"))
    ownership_problems(inv, snapshot, problems)
    device_problems, warnings = _devices(inv, snapshot, checks)
    return Findings(tuple(problems + device_problems + _annotated(inv)), tuple(warnings))
