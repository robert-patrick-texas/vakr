r"""Write an export as TOML or CSV, byte for byte the same for the same storage (META-010, E.38).

TOML follows the ``inventory.toml`` format: ``[[sites]]``, ``[[devices]]``,
and ``[[interface_annotations]]`` with their ``uuid``, keys that hold a
default left out, and each record's owner in a comment above it. A
``--owner file`` export is therefore an ``inventory.toml`` that plans as no
changes; any export plans as no changes through ``vakr inventory import``.
CSV has the columns of ``vakr.inventory.csv_columns`` in their order, with
``managed_by``, every cell formula-guarded. Neither format carries a
timestamp or a version, so two exports of the same storage are identical.

Dependencies:
    json: TOML basic-string escapes are a superset of JSON's.
    vakr.inventory.csv_codec, csv_columns, export_data.

Safety:
    Credential names only; never a secret.

Example:
    >>> toml_string('say "hi"\\n')
    '"say \\\\"hi\\\\"\\\\n"'
"""

import json
from collections.abc import Sequence

from vakr.domain.enums import AdminState, InterestClass
from vakr.domain.inventory import Site
from vakr.inventory.csv_codec import write_rows
from vakr.inventory.csv_columns import ANNOTATION_COLUMNS, DEVICE_COLUMNS, DEVICES
from vakr.inventory.export_data import ExportSet
from vakr.inventory.snapshot import StoredDevice, StoredInterface
from vakr.policy.settings import format_seconds


def toml_string(text: str) -> str:
    """Quote a string as a TOML basic string.

    Args:
        text: The value.

    Returns:
        str: The quoted string; ``tomllib`` reads back ``text``.

    Raises:
        None
    """
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(value: object) -> str:
    """Render a TOML value: string, list of strings, integer, or boolean.

    Args:
        value: The value.

    Returns:
        str: Its TOML text.

    Raises:
        None
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(toml_string(str(v)) for v in value) + "]"
    return toml_string(str(value))


def _table(name: str, owner: str, pairs: Sequence[tuple[str, object]]) -> list[str]:
    """Render one array-of-tables entry, leaving out empty values.

    Args:
        name: ``sites``, ``devices``, or ``interface_annotations``.
        owner: The record's owner, for the comment line.
        pairs: ``(key, value)``; ``None``, ``""``, ``()``, and ``False`` are left out.

    Returns:
        list[str]: The lines, ending with a blank one.

    Raises:
        None
    """
    lines = [f"# {owner}", f"[[{name}]]"]
    # Identity tests, because 0 == False: a display rank of 0 is a value, not a default.
    empty = [v is None or v is False or v in ("", ()) for _, v in pairs]
    lines += [f"{k} = {_value(v)}" for (k, v), skip in zip(pairs, empty, strict=True) if not skip]
    return [*lines, ""]


def _seconds(value: int | None) -> str | None:
    """Render an interval override as a duration, such as ``2m``.

    Args:
        value: Seconds, or ``None``.

    Returns:
        str | None: The duration.

    Raises:
        None
    """
    return None if value is None else format_seconds(value)


def _site_pairs(s: Site) -> list[tuple[str, object]]:
    """Return a site's TOML keys.

    Args:
        s: The site.

    Returns:
        list[tuple[str, object]]: Keys in a fixed order.

    Raises:
        None
    """
    return [("name", s.name), ("uuid", str(s.id)), ("code", s.code), ("region", s.region),
            ("timezone", s.timezone), ("description", s.description)]  # fmt: skip


def _device_pairs(stored: StoredDevice) -> list[tuple[str, object]]:
    """Return a device's TOML keys; the CSV columns use the same values.

    Args:
        stored: The device.

    Returns:
        list[tuple[str, object]]: Keys in a fixed order.

    Raises:
        None
    """
    d = stored.device
    state = None if d.admin_state is AdminState.ENABLED else d.admin_state.value
    return [
        ("name", d.name), ("uuid", str(d.id)), ("address", d.poll_address), ("site", stored.site),
        ("credential", d.credential_ref), ("admin_state", state),
        ("interval", _seconds(d.poll_interval_s)),
        ("discovery_interval", _seconds(d.discovery_interval_s)),
        ("reachability", d.reachability and d.reachability.value),
        ("address_family", d.address_family and d.address_family.value),
        ("tags", stored.tags), ("groups", stored.groups), ("comment", d.comment),
    ]  # fmt: skip


def _annotation_pairs(stored: StoredInterface) -> list[tuple[str, object]]:
    """Return an interface annotation's TOML keys.

    Args:
        stored: The interface.

    Returns:
        list[tuple[str, object]]: Keys in a fixed order.

    Raises:
        None
    """
    i, a = stored.interface, stored.interface.annotation
    return [
        ("device", stored.device), ("ifname", i.if_name), ("uuid", str(i.id)),
        ("interest", None if a.interest is InterestClass.SECONDARY else a.interest.value),
        ("wan_circuit", a.is_wan_circuit), ("sticky", a.sticky), ("provider", a.provider),
        ("circuit_id", a.circuit_id), ("contracted_bps", a.contracted_bps),
        ("capacity_override_bps", a.capacity_override_bps), ("display_rank", a.display_rank),
        ("public_dashboard_eligible", a.public_dashboard), ("tags", stored.tags),
        ("comment", i.comment),
    ]  # fmt: skip


def export_toml(found: ExportSet, kind: str | None) -> str:
    """Write an export in the ``inventory.toml`` format.

    Args:
        found: What to export.
        kind: ``devices`` (sites and devices), ``annotations``, or ``None`` for all.

    Returns:
        str: The TOML text.

    Raises:
        None
    """
    sites = found.sites if kind != "annotations" else ()
    devices = found.devices if kind != "annotations" else ()
    notes = found.interfaces if kind != DEVICES else ()
    # Without the annotations, applying a partial export as inventory.toml would release claims.
    usage = ("an --owner file export is also an inventory.toml" if kind is None
             else f"only {kind}: not a whole inventory.toml")  # fmt: skip
    lines = [f"# vakr inventory export (owner {found.owner}): {len(sites)} site(s), "
             f"{len(devices)} device(s), {len(notes)} annotation(s).",
             f"# Plan it with `vakr inventory import FILE`; {usage}.", ""]  # fmt: skip
    for s in sites:
        lines += _table("sites", str(s.managed_by), _site_pairs(s))
    for d in devices:
        lines += _table("devices", str(d.managed_by), _device_pairs(d))
    for i in notes:
        lines += _table("interface_annotations", str(i.interface.managed_by), _annotation_pairs(i))
    return "\n".join(lines).rstrip("\n") + "\n"


def _cell(value: object) -> str:
    """Render one CSV cell: empty for none, ``true``/``false``, ``;``-joined lists.

    Args:
        value: The value.

    Returns:
        str: The cell text, before the formula guard.

    Raises:
        None
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return ";".join(value) if isinstance(value, tuple) else str(value)


def export_csv(found: ExportSet, kind: str) -> str:
    """Write the devices or the annotations of an export as CSV.

    Args:
        found: What to export.
        kind: ``devices`` or ``annotations``.

    Returns:
        str: The CSV text: header, then one row per record.

    Raises:
        None
    """
    if kind == DEVICES:
        header = list(DEVICE_COLUMNS)
        records = [(dict(_device_pairs(d)) | {"managed_by": str(d.managed_by),
                    "admin_state": d.device.admin_state.value}) for d in found.devices]  # fmt: skip
    else:
        header = [c for c in ANNOTATION_COLUMNS if c != "high_priority"]
        records = [dict(_annotation_pairs(i)) | {
            "managed_by": str(i.interface.managed_by), "alias": i.interface.if_alias,
            "interest": i.interface.annotation.interest.value,
        } for i in found.interfaces]  # fmt: skip
    return write_rows(header, [[_cell(r.get(c)) for c in header] for r in records])
