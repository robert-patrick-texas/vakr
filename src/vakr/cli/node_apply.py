"""Carry out a checked role change: config, role record, resync marker, audit, restart (E.39).

``vakr node promote`` and ``demote`` decide in ``vakr.replication.promotion``
and then call ``apply_change``, which, in order:

1. prints the preconditions found, and stops on a refusal;
2. asks at a terminal (``--yes`` skips it);
3. for a demotion, writes ``resync.json`` for the collector first: if it
   cannot, nothing changes and the command exits 1, because a demoted
   node without it would keep its divergent metadata unreported;
4. rewrites ``[node]`` through ``vakr.config.node_edit`` (backup first),
   removing the marker again if that fails;
5. appends the change, with the settings before and after, to
   ``node-role.json`` (after a promotion it removes a stale marker);
6. audits it (``node.promote`` or ``node.demote``) and restarts the two
   units that read the role, unless ``--no-restart``.

A failed role-record or audit write is a warning: the configuration
already changed, and the other of the two records says so on its own.

Dependencies:
    vakr.config.node_edit, vakr.replication.role_record, vakr.cli.node_support.

Safety:
    Nothing is written when a check fails. The records hold URLs, roles, and
    paths, never the token.

Example:
    >>> probe_fields(PartnerProbe("https://east.example.net", False))["answered"]
    False
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vakr.cli import node_support
from vakr.cli.user_support import cli_actor
from vakr.config import project
from vakr.config.node import NodeSettings
from vakr.config.node_edit import NodeEdit, NodeEditError, plan_node_edit, write_node_edit
from vakr.paths import InstallPaths
from vakr.replication import role_record
from vakr.replication.promotion import Decision, PartnerProbe

EXIT_OK, EXIT_REFUSED = 0, 1


def _refuse(message: str) -> int:
    """Print a refusal.

    Args:
        message: Why.

    Returns:
        int: 1.

    Raises:
        None
    """
    sys.stdout.flush()  # the preconditions first, then why, even through a pipe
    print(f"vakr: {message}", file=sys.stderr)
    return EXIT_REFUSED


def apply_change(args: argparse.Namespace, paths: InstallPaths, node: NodeSettings,
                 found: Decision, action: str, wanted: dict[str, str | None],
                 probe: PartnerProbe, extra: dict[str, Any]) -> int:  # fmt: skip
    """Report the preconditions, then write, record, audit, and restart.

    Args:
        args: Parsed arguments (``yes``, ``no_restart``).
        paths: The layout.
        node: The settings before.
        found: The decision.
        action: ``promote`` or ``demote``.
        wanted: The ``[node]`` changes.
        probe: What the partner answered.
        extra: More fields for the role record.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    for line in found.lines:
        print(line)
    if not found.allowed:
        return _refuse("; ".join(found.refusals))
    for why in found.forced:
        print(f"forced past: {why}")
    if not node_support.confirm(f"{action.capitalize()} this node now?", args.yes):
        return _refuse("nothing changed")
    at, actor = datetime.now(UTC).isoformat(timespec="seconds"), cli_actor()
    try:
        edit = plan_node_edit(paths.config_dir, wanted)
        backup = write_role(paths, action, edit, {"at": at, "actor": actor})
    except (NodeEditError, OSError) as exc:
        return _refuse(f"configuration not changed: {exc}")
    change = {"at": at, "action": action, "actor": actor, "forced": bool(found.forced),
              "before": settings_of(node), "after": settings_of(edit.after),
              "partner": probe_fields(probe), "config_backup": str(backup) if backup else None,
              "files": edit.files, **extra}  # fmt: skip
    record_change(paths, action, change, edit.after)
    print(f"{action}d: node.role = {edit.after.role} in {', '.join(edit.files) or 'no file'}; "
          f"backup {backup or 'not needed'}")  # fmt: skip
    audit_and_restart(args.no_restart, action, change, paths)
    return EXIT_OK


def settings_of(node: NodeSettings) -> dict[str, str]:
    """Describe node settings for the role record (the old and new values).

    Args:
        node: The settings.

    Returns:
        dict: ``role``, ``position``, ``primary_url``, ``peer_url``.

    Raises:
        None
    """
    return {"role": node.role, "position": node.position, "primary_url": node.primary_url,
            "peer_url": node.peer_url}  # fmt: skip


def probe_fields(probe: PartnerProbe) -> dict[str, Any]:
    """Describe the partner's answer for the role record.

    Args:
        probe: The answer.

    Returns:
        dict: Its fields.

    Raises:
        None
    """
    return {"url": probe.url, "answered": probe.answered, "role": probe.role,
            "position": probe.position, "detail": probe.detail}  # fmt: skip


def write_role(paths: InstallPaths, action: str, edit: NodeEdit,
               who: dict[str, str]) -> Path | None:  # fmt: skip
    """Write the new ``[node]`` settings; a demotion writes its resync marker first.

    A demotion without its marker would pull in ordinary mode and keep the
    divergent rows with no report or backup, so the marker comes first and,
    if the configuration then cannot be written, goes again (MPOS-003, E.39).

    Args:
        paths: The layout.
        action: ``promote`` or ``demote``.
        edit: The checked change.
        who: ``at`` and ``actor`` for the marker.

    Returns:
        Path | None: The configuration backup, or ``None`` when nothing changed.

    Raises:
        OSError: If the marker or the configuration cannot be written; the
            configuration is then unchanged.
    """
    marker = paths.state_dir / role_record.RESYNC_FILE
    if action == "demote":
        try:
            role_record.write_json(marker, {**who, "primary_url": edit.after.primary_url})
        except OSError as exc:
            raise OSError(f"cannot write the resync marker {marker} ({exc}); without it the "
                          "demoted node would keep its divergent metadata") from None  # fmt: skip
    try:
        return write_node_edit(paths.config_dir, edit, project.backup_root(paths))
    except OSError:
        if action == "demote":
            marker.unlink(missing_ok=True)
        raise


def record_change(paths: InstallPaths, action: str, change: dict[str, Any],
                  after: NodeSettings) -> None:  # fmt: skip
    """Record the change; after a promotion, drop a stale resync marker.

    Args:
        paths: The layout.
        action: ``promote`` or ``demote``.
        change: The role record entry.
        after: The settings now.

    Returns:
        None

    Raises:
        None
    """
    if action == "demote":
        print(
            f"the collector replaces this node's metadata with {after.primary_url}'s at "
            "its next successful pull, after a backup; `vakr node status` reports it"
        )
    try:
        role_record.append_change(paths.state_dir / role_record.ROLE_FILE, change)
        if action == "promote":
            (paths.state_dir / role_record.RESYNC_FILE).unlink(missing_ok=True)
    except OSError as exc:
        print(f"vakr: warning: cannot write the role record: {exc}", file=sys.stderr)


def audit_and_restart(no_restart: bool, action: str, change: dict[str, Any],
                      paths: InstallPaths) -> None:  # fmt: skip
    """Audit the change, then restart the two units unless ``--no-restart``.

    Args:
        no_restart: ``--no-restart``.
        action: ``promote`` or ``demote``.
        change: The role record entry.
        paths: The layout, to name the role record in a warning.

    Returns:
        None

    Raises:
        None
    """
    before, after = change["before"], change["after"]
    summary = f"{action}: {before['role']} -> {after['role']}; primary " \
        f"{after['primary_url'] or 'this node'}; forced {change['forced']}"  # fmt: skip
    if (problem := node_support.audit_change(f"node.{action}", summary, datetime.now(UTC),
                                             change["actor"])) is not None:  # fmt: skip
        print(f"vakr: warning: {problem}; the change is recorded in "
              f"{paths.state_dir / role_record.ROLE_FILE}", file=sys.stderr)  # fmt: skip
    if no_restart:
        print("not restarted (--no-restart): run `sudo systemctl restart "
              f"{' '.join(node_support.UNITS)}`")  # fmt: skip
    else:
        print(node_support.restart_services())
