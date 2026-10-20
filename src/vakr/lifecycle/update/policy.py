"""Decide whether a release should be installed: channel, hold, retry rules.

The rules, in order:

* Automatic selection takes the newest non-draft release on the channel at
  or below ``hold_version`` (UPD-008) and never downgrades: if it is not
  newer than the installed version there is nothing to do.
* An explicit target (``--version`` or ``--file``) may reinstall or
  downgrade, unless ``--if-newer`` is given. Interactively it may go past
  the hold, because an operator chose it; in unattended (timer) mode the
  hold is absolute.
* In unattended mode, a version whose install already failed is skipped
  until a newer release appears (UPD-006), and a release whose notes carry
  the manual-update marker is skipped (UPD-005).

Dependencies:
    vakr.lifecycle.update.versions: Ordering.

Safety:
    Pure functions with no I/O.

Example:
    >>> req = Request(unattended=True)
    >>> gate(Version.parse("0.3.0"), Version.parse("0.2.0"), req, {"0.3.0"}).status
    'skipped'
"""

from collections.abc import Collection
from dataclasses import dataclass

from vakr.lifecycle.update.versions import Version

MANUAL_MARKER = "manual-update-required"
"""Text in release notes that keeps timer-driven updates from installing it."""


@dataclass(frozen=True, slots=True)
class Request:
    """What the operator (or timer) asked for.

    Attributes:
        channel: ``"prerelease"`` or ``"stable"``.
        hold: ``hold_version`` ceiling, or ``None``.
        version: Explicit ``--version`` target, or ``None``.
        if_newer: Install only when the target is newer than installed.
        unattended: Timer mode: no prompts, strict hold and retry rules.
    """

    channel: str = "prerelease"
    hold: Version | None = None
    version: Version | None = None
    if_newer: bool = False
    unattended: bool = False


@dataclass(frozen=True, slots=True)
class Skip:
    """Why no install happens.

    Attributes:
        status: ``"up-to-date"`` (nothing newer) or ``"skipped"`` (a rule
            held back an available release).
        reason: Operator-readable explanation.
    """

    status: str
    reason: str


def gate(
    target: Version,
    current: Version,
    request: Request,
    failed: Collection[str],
    *,
    explicit: bool = False,
    notes: str = "",
    marker: str = MANUAL_MARKER,
) -> Skip | None:
    """Apply the install rules to a chosen target.

    Args:
        target: Version that would be installed.
        current: Installed version.
        request: Operator or timer request.
        failed: Semver strings of versions whose install failed.
        explicit: Whether the target came from ``--version`` or ``--file``.
        notes: Release notes of the target, for the manual marker.
        marker: Manual-update marker text; empty disables the rule.

    Returns:
        Skip | None: Why to skip, or ``None`` to proceed.

    Raises:
        None
    """
    if request.unattended and request.hold is not None and target > request.hold:
        return Skip("skipped", f"{target} is beyond hold_version {request.hold}")
    if (request.if_newer or not explicit) and target <= current:
        return Skip("up-to-date", f"installed {current} is up to date (newest is {target})")
    if request.unattended and target.semver() in failed:
        return Skip("skipped", f"{target} failed to install before; waiting for a newer release")
    if request.unattended and marker and marker in notes:
        return Skip("skipped", f"{target} release notes require a manual update")
    return None


def no_release(request: Request) -> Skip:
    """Explain that the channel offers nothing to install.

    Args:
        request: Operator or timer request.

    Returns:
        Skip: ``up-to-date`` with the channel and hold in the reason.

    Raises:
        None
    """
    held = f" at or below hold_version {request.hold}" if request.hold else ""
    return Skip("up-to-date", f"no release on the {request.channel} channel{held}")
