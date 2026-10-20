"""Operator output for inventory plans, findings, and listings (18.3, META-010).

Text output is for people: one summary line, one line per change, then the
annotations still waiting with the command that unblocks each. JSON output
carries the same content for scripts, with stable keys. The order is the
plan's, which is deterministic, so two runs on the same inputs print the
same bytes (CFG-010).

Dependencies:
    vakr.inventory.plan_model, validate: Plans and findings.

Safety:
    Prints what the operator wrote and record UUIDs; never a secret.

Example:
    >>> from vakr.inventory.plan_model import InventoryPlan
    >>> plan_lines(InventoryPlan("inventory.toml"))
    ['inventory.toml: no changes']
"""

from typing import Any

from vakr.inventory.errors import render_issues
from vakr.inventory.plan_model import Change, InventoryPlan
from vakr.inventory.validate import Findings


def _change_line(change: Change) -> str:
    """Render one change.

    Args:
        change: The change.

    Returns:
        str: Such as ``update device edge1: interval_s: none -> 120``.

    Raises:
        None
    """
    details = "; ".join(change.details)
    return f"  {change.action.value} {change.kind} {change.name}" + (
        f": {details}" if details else ""
    )


def plan_lines(plan: InventoryPlan) -> list[str]:
    """Render a plan as text lines.

    Args:
        plan: The plan.

    Returns:
        list[str]: Summary, changes, and waiting annotations.

    Raises:
        None
    """
    lines = [f"{plan.source}: {plan.summary}"]
    lines.extend(_change_line(c) for c in plan.changes)
    if plan.waiting:
        lines.append(f"waiting ({len(plan.waiting)} annotation(s) not applied yet):")
        lines.extend(f"  {w.entry.device} {w.entry.ifname}: {w.reason}" for w in plan.waiting)
    return lines


def warning_lines(source: str, findings: Findings) -> list[str]:
    """Render warnings, each starting ``warning:``.

    Args:
        source: The file name.
        findings: Validation findings.

    Returns:
        list[str]: One line per warning.

    Raises:
        None
    """
    return [f"warning: {line}" for line in render_issues(source, findings.warnings)]


def plan_dict(plan: InventoryPlan, findings: Findings | None = None) -> dict[str, Any]:
    """Render a plan as JSON-compatible data.

    Args:
        plan: The plan.
        findings: Warnings to include, if any.

    Returns:
        dict[str, Any]: ``source``, ``summary``, ``changes``, ``waiting``, ``warnings``.

    Raises:
        None
    """
    return {
        "source": plan.source,
        "summary": plan.summary,
        "unchanged": plan.unchanged,
        "changes": [
            {
                "action": c.action.value,
                "kind": c.kind,
                "name": c.name,
                "id": None if c.record_id is None else str(c.record_id),
                "details": list(c.details),
            }
            for c in plan.changes
        ],
        "waiting": [
            {
                "device": w.entry.device,
                "ifname": w.entry.ifname,
                "reason": w.reason,
                "interface_id": None if w.interface_id is None else str(w.interface_id),
            }
            for w in plan.waiting
        ],
        "warnings": [] if findings is None else render_issues(plan.source, findings.warnings),
    }
