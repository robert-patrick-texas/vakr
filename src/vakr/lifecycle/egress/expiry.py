"""Summarize the expiry of operator-trusted CAs for a health check (EGRESS-006).

An enterprise inspection CA that silently expires breaks every update on
every host behind that proxy at once. The health check that runs every
minute reads the CA directory (no network I/O) and turns amber 30 days
before the first expiry and red once one has expired, so the security team
can rotate it in time.

Dependencies:
    vakr.lifecycle.egress.castore, certinfo: Installed CAs and expiry rules.

Safety:
    Read-only; reports names and dates only.

Example:
    >>> expiry_report(Path("/nonexistent"), now).severity  # doctest: +SKIP
    'ok'
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vakr.lifecycle.egress.castore import list_installed
from vakr.lifecycle.egress.certinfo import expiry_state


@dataclass(frozen=True, slots=True)
class ExpiryReport:
    """Aggregate state of the CA directory.

    Attributes:
        severity: ``"ok"``, ``"warning"``, or ``"critical"``.
        summary: One operator-readable sentence.
        min_days_left: Days until the earliest expiry, or ``None`` when no
            readable CA is installed.
    """

    severity: str
    summary: str
    min_days_left: float | None = None


def expiry_report(ca_dir: Path, now: datetime) -> ExpiryReport:
    """Classify every CA in ``ca_dir`` and report the worst case.

    Args:
        ca_dir: Application CA directory.
        now: Current UTC time.

    Returns:
        ExpiryReport: ``critical`` if any CA has expired; ``warning`` if one
        expires within 30 days or a file is unreadable; else ``ok``.

    Raises:
        None
    """
    entries = list_installed(ca_dir)
    if not entries:
        return ExpiryReport("ok", "no extra trusted CAs")
    unreadable = [e.name for e in entries if e.summary is None]
    dated = sorted((e.summary.not_after, e.name) for e in entries if e.summary is not None)
    states = {name: expiry_state(after, now) for after, name in dated}
    expired = [n for n, s in states.items() if s == "expired"]
    expiring = [n for n, s in states.items() if s == "expiring"]
    days = round((dated[0][0] - now).total_seconds() / 86400, 1) if dated else None
    if expired:
        return ExpiryReport("critical", f"expired CA: {', '.join(expired)}", days)
    problems = [f"expires within 30 days: {', '.join(expiring)}"] if expiring else []
    problems += [f"unreadable: {', '.join(unreadable)}"] if unreadable else []
    if problems:
        return ExpiryReport("warning", "; ".join(problems), days)
    return ExpiryReport("ok", f"{len(entries)} trusted CA(s) valid", days)
