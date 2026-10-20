"""What an SNMP request produced: varbinds, the first error, and effort counts.

Results keep every varbind that did arrive (SNMP-014), name the OIDs that
did not, and count the effort spent: requests, responses, timeouts, retries,
and request-size reductions (SNMP-017, SNMP-018). The SNMP device
observation (blueprint 10.2) is built from these counts.

Dependencies:
    dataclasses: Immutable results.
    vakr.domain.quality: ``RETRIED`` and ``REQUEST_SIZE_REDUCED`` flags.
    vakr.ports.snmp.errors, vakr.ports.snmp.values: Errors, OIDs, varbinds.

Safety:
    Results never hold a credential.

Example:
    >>> SnmpStats(requests=3, responses=2, timeouts=1, retries=1).quality.name
    'RETRIED'
"""

from dataclasses import dataclass, field

from vakr.domain.quality import Quality
from vakr.ports.snmp.errors import SnmpError
from vakr.ports.snmp.values import Oid, VarBind


@dataclass(frozen=True, slots=True)
class SnmpStats:
    """Effort spent on SNMP requests (10.2, SNMP-018).

    Attributes:
        requests: PDUs sent, retries included.
        responses: PDUs answered.
        timeouts: Attempts that got no answer in time.
        retries: Attempts sent after a timeout.
        size_reductions: Times the request size was reduced (SNMP-017).
        elapsed_s: Seconds spent waiting for answers (monotonic clock).
    """

    requests: int = 0
    responses: int = 0
    timeouts: int = 0
    retries: int = 0
    size_reductions: int = 0
    elapsed_s: float = 0.0

    def __add__(self, other: SnmpStats) -> SnmpStats:
        """Add two counts, for example two stages of one poll.

        Args:
            other: The counts to add.

        Returns:
            SnmpStats: The sums.

        Raises:
            None
        """
        return SnmpStats(
            self.requests + other.requests,
            self.responses + other.responses,
            self.timeouts + other.timeouts,
            self.retries + other.retries,
            self.size_reductions + other.size_reductions,
            self.elapsed_s + other.elapsed_s,
        )

    def __sub__(self, other: SnmpStats) -> SnmpStats:
        """Subtract an earlier snapshot, giving the effort spent since.

        Args:
            other: The earlier snapshot.

        Returns:
            SnmpStats: The differences.

        Raises:
            None
        """
        return SnmpStats(
            self.requests - other.requests,
            self.responses - other.responses,
            self.timeouts - other.timeouts,
            self.retries - other.retries,
            self.size_reductions - other.size_reductions,
            self.elapsed_s - other.elapsed_s,
        )

    @property
    def quality(self) -> Quality:
        """Return the quality flags this effort earns (SNMP-017, SNMP-018).

        Args:
            None

        Returns:
            Quality: ``RETRIED`` and/or ``REQUEST_SIZE_REDUCED``, or ``NONE``.

        Raises:
            None
        """
        flags = Quality.RETRIED if self.retries else Quality.NONE
        if self.size_reductions:
            flags |= Quality.REQUEST_SIZE_REDUCED
        return flags


@dataclass(frozen=True, slots=True)
class GetResult:
    """The outcome of a GET for a list of exact OIDs.

    Attributes:
        requested: OIDs asked for, in order, without duplicates.
        varbinds: Varbinds received, in request order. ``noSuchObject`` and
            ``noSuchInstance`` answers are included: they are answers.
        failed: OIDs that got no answer (timeout, error, or abandoned).
        error: The first failure, or ``None`` when every OID was answered.
        stats: Effort spent on this GET.
    """

    requested: tuple[Oid, ...]
    varbinds: tuple[VarBind, ...]
    failed: tuple[Oid, ...] = ()
    error: SnmpError | None = None
    stats: SnmpStats = field(default_factory=SnmpStats)

    @property
    def complete(self) -> bool:
        """Whether every requested OID was answered.

        Args:
            None

        Returns:
            bool: True when ``failed`` is empty.

        Raises:
            None
        """
        return not self.failed

    def by_oid(self) -> dict[Oid, VarBind]:
        """Index the received varbinds by OID.

        Args:
            None

        Returns:
            dict[Oid, VarBind]: One entry per answered OID.

        Raises:
            None
        """
        return {vb.oid: vb for vb in self.varbinds}


@dataclass(frozen=True, slots=True)
class WalkResult:
    """The outcome of walking one subtree with GETBULK.

    Attributes:
        root: The subtree walked.
        varbinds: Varbinds inside the subtree, in OID order; kept even when
            the walk stopped early (DISC-008).
        complete: True when the walk reached the end of the subtree.
        error: Why the walk stopped early, or ``None``.
        stats: Effort spent on this walk.
    """

    root: Oid
    varbinds: tuple[VarBind, ...]
    complete: bool
    error: SnmpError | None = None
    stats: SnmpStats = field(default_factory=SnmpStats)
