"""Shared plumbing for ``vakr device`` and ``vakr interface annotate|tag`` (27.4, META-008).

Options become the edit's fields with the ``inventory.toml`` keys; an
option given as an empty string clears an optional field, and a list
option takes comma-separated names (``--tags wan,isp``; ``--tags ""``
clears them). Records print as text, or as the API's JSON with ``--json``,
so a script sees the same shape from both.

Dependencies:
    vakr.cli.inventory_support: Storage, actor, and exit codes.
    vakr.inventory.edits: The editors and records.

Safety:
    Prints credential names, never secrets; the credential file is read
    for its names only (``checks_for``).

Example:
    >>> fields_from(argparse.Namespace(site="", tags="wan, isp", comment=None), ("site", "comment"))
    {'site': None, 'tags': ['wan', 'isp']}
"""

import argparse
import json
from collections.abc import Sequence
from typing import Any

from vakr.config.loader import load_effective_config
from vakr.inventory.edits.ownership import owner_text
from vakr.inventory.edits.records import AnnotationRecord, DeviceRecord
from vakr.inventory.settings import checks_for
from vakr.inventory.validate import Checks
from vakr.paths import InstallPaths

LISTS = ("tags", "groups")
"""Options that take comma-separated names."""


def fields_from(args: argparse.Namespace, keys: Sequence[str]) -> dict[str, Any]:
    """Collect the fields an edit sets from parsed options.

    Args:
        args: Parsed options; ``None`` means the option was not given.
        keys: Scalar option names, as ``inventory.toml`` keys.

    Returns:
        dict[str, Any]: The fields; an empty string becomes ``None`` (clear).

    Raises:
        None
    """
    fields: dict[str, Any] = {}
    for key in keys:
        value = getattr(args, key, None)
        if value is not None:
            fields[key] = (value.strip() or None) if isinstance(value, str) else value
    for key in LISTS:
        value = getattr(args, key, None)
        if value is not None:
            fields[key] = [n.strip() for n in value.split(",") if n.strip()]
    return fields


def edit_checks(args: argparse.Namespace) -> Checks:
    """Read credential names and the target guardrails, as ``vakr inventory apply`` does.

    Args:
        args: ``config_dir`` and ``dev``.

    Returns:
        Checks: The checks.

    Raises:
        ConfigError: If the configuration is invalid.
        ValueError: If a guardrail setting is invalid.
    """
    paths = InstallPaths.from_environment()
    config = load_effective_config(args.config_dir or paths.config_dir)
    return checks_for(config, paths.secrets_dir, development=args.dev)


def device_lines(record: DeviceRecord) -> list[str]:
    """Render a device record as text.

    Args:
        record: The record.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    d, s = record.stored.device, record.stored
    pairs = (("address", d.poll_address), ("site", s.site), ("credential", d.credential_ref),
             ("tags", ", ".join(s.tags)), ("groups", ", ".join(s.groups)),
             ("interval", d.poll_interval_s and f"{d.poll_interval_s}s"),
             ("discovery_interval", d.discovery_interval_s and f"{d.discovery_interval_s}s"),
             ("reachability", d.reachability), ("address_family", d.address_family),
             ("comment", d.comment))  # fmt: skip
    owner = owner_text(d.managed_by)
    head = f"{d.name} ({d.id}): {d.admin_state.value}, {owner}, version {d.version}"
    return [head, *(f"  {k}: {v or '-'}" for k, v in pairs), *outcome(record.changes, record)]


def annotation_lines(record: AnnotationRecord) -> list[str]:
    """Render an interface's annotation as text.

    Args:
        record: The record.

    Returns:
        list[str]: Lines.

    Raises:
        None
    """
    i, a = record.interface, record.interface.annotation
    pairs = (("interest", a.interest.value), ("sticky", a.sticky),
             ("wan_circuit", a.is_wan_circuit), ("provider", a.provider),
             ("circuit_id", a.circuit_id), ("contracted_bps", a.contracted_bps),
             ("capacity_override_bps", a.capacity_override_bps), ("tags", ", ".join(record.tags)),
             ("description_tags", ", ".join(record.description_tags)),
             ("comment", i.comment))  # fmt: skip
    head = (f"{record.device} {i.if_name or i.if_descr} ({i.id}): {owner_text(i.managed_by)}, "
            f"version {i.version}")  # fmt: skip
    return [head, *(f"  {k}: {'-' if v in (None, '') else v}" for k, v in pairs),
            *outcome(record.changes, None)]  # fmt: skip


def outcome(changes: tuple[str, ...], record: DeviceRecord | None) -> list[str]:
    """Render what an edit changed and warned about.

    Args:
        changes: Change lines.
        record: The device record, for its warnings.

    Returns:
        list[str]: ``changed:`` and ``warning:`` lines.

    Raises:
        None
    """
    warnings = record.warnings if record is not None else ()
    return [*(f"changed: {c}" for c in changes), *(f"warning: {w}" for w in warnings)]


def print_record(args: argparse.Namespace, record: DeviceRecord | AnnotationRecord) -> None:
    """Print a record as text, or as the API's JSON with ``--json``.

    Args:
        args: Parsed options with ``json``.
        record: The record.

    Returns:
        None

    Raises:
        None
    """
    if getattr(args, "json", False):
        from vakr.api.schemas_inventory import annotation_json, device_json

        data = device_json(record) if isinstance(record, DeviceRecord) else annotation_json(record)
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    lines = device_lines(record) if isinstance(record, DeviceRecord) else annotation_lines(record)
    print("\n".join(lines))
