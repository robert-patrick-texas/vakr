"""Value objects for self-health results and their Nagios plugin rendering.

CheckMK and Nagios run plugins that print one status line and exit with
0 (OK), 1 (WARNING), 2 (CRITICAL), or 3 (UNKNOWN). vakr maps every internal
self-check onto that contract so the team's existing alerting can page on
collector, spool, database, replication, update, or certificate trouble
without vakr needing an alerting engine of its own (OPS-001).

Dependencies:
    dataclasses, enum: Immutable, typed results instead of loose strings.

Safety:
    Summaries and performance data are operator-facing; check authors must
    never place credentials, community strings, or tokens in them.

Example:
    >>> r = CheckResult("spool", Status.OK, "12% used", (PerfDatum("spool_pct", 12.0, "%"),))
    >>> nagios_line((r,))
    'VAKR OK - spool: 12% used | spool_pct=12.0%'
"""

from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    """Severity of one self-check, ordered by the Nagios plugin exit codes.

    Members are immutable singletons. ``UNKNOWN`` ranks above ``CRITICAL``
    only for exit-code purposes; ``worst`` treats CRITICAL as most severe so a
    real failure is never masked by an unrelated unknown.
    """

    OK = 0
    WARNING = 1
    CRITICAL = 2
    UNKNOWN = 3

    @property
    def exit_code(self) -> int:
        """Return the Nagios plugin exit code for this status.

        Args:
            None

        Returns:
            int: 0, 1, 2, or 3.

        Raises:
            None
        """
        return self.value


_SEVERITY_ORDER = (Status.OK, Status.UNKNOWN, Status.WARNING, Status.CRITICAL)


@dataclass(frozen=True, slots=True)
class PerfDatum:
    """One Nagios performance-data value, such as ``spool_pct=12.0%``.

    Attributes:
        label: Metric name without spaces or ``=``.
        value: Measured value in the stated unit.
        unit: Nagios unit suffix (``""``, ``"%"``, ``"s"``, ``"B"``, ...).
    """

    label: str
    value: float
    unit: str = ""

    def render(self) -> str:
        """Render the datum in Nagios ``label=valueunit`` form.

        Args:
            None

        Returns:
            str: The formatted performance-data token.

        Raises:
            None
        """
        return f"{self.label}={self.value}{self.unit}"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one self-check at one moment.

    Attributes:
        name: Short stable check name, for example ``"spool"``.
        status: Severity of the outcome.
        summary: One operator-readable sentence; never contains secrets.
        perfdata: Optional measurements for trending in CheckMK/Nagios.
    """

    name: str
    status: Status
    summary: str
    perfdata: tuple[PerfDatum, ...] = ()


def worst(results: tuple[CheckResult, ...]) -> Status:
    """Return the most severe status across several check results.

    Args:
        results: Results from one evaluation of all checks.

    Returns:
        Status: ``OK`` for an empty tuple, otherwise the most severe status,
        where CRITICAL > WARNING > UNKNOWN > OK.

    Raises:
        None
    """
    if not results:
        return Status.OK
    return max((r.status for r in results), key=_SEVERITY_ORDER.index)


def nagios_line(results: tuple[CheckResult, ...]) -> str:
    """Render results as a single Nagios plugin output line.

    Non-OK results are listed first so the most important problem is visible
    in notification subjects that truncate the line.

    Args:
        results: Results from one evaluation of all checks.

    Returns:
        str: ``VAKR <STATUS> - name: summary; ... | perfdata ...``.

    Raises:
        None
    """
    overall = worst(results)
    ordered = sorted(results, key=lambda r: -_SEVERITY_ORDER.index(r.status))
    text = "; ".join(f"{r.name}: {r.summary}" for r in ordered) or "no checks"
    perf = " ".join(d.render() for r in results for d in r.perfdata)
    line = f"VAKR {overall.name} - {text}"
    return f"{line} | {perf}" if perf else line
