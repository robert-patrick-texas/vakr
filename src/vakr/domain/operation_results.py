"""What a finished rediscovery found and changed: the result of an admin operation (DISC-T03).

``vakr.domain.operations.Operation`` carries one when it ``succeeded``; the
web UI, the API, and ``vakr inventory rediscover --wait`` show these counts
to the administrator who asked.

Dependencies:
    vakr.domain.checks: Integer bounds.

Safety:
    Counts only.

Example:
    >>> OperationResult(24, new=1).missing
    0
"""

from dataclasses import dataclass

from vakr.domain.checks import require_int
from vakr.domain.errors import ValidationError

MAX_COUNT = 1_000_000
"""Largest count a result holds; far beyond any device (E.32)."""


@dataclass(frozen=True, slots=True)
class OperationResult:
    """What a finished rediscovery found and changed (DISC-T03).

    Attributes:
        interfaces: Interfaces the agent reported.
        new: Interfaces that got a new identity.
        renumbered: Known interfaces matched at another ``ifIndex``.
        missing: Known interfaces the agent no longer reported.
        reviews_opened: Identity cases left for an administrator (IFID-007).
        capability_changes: Interfaces whose counter capabilities changed (DISC-009).
        partial: Whether some table came back incomplete.
        duration_ms: How long the discovery took.
    """

    interfaces: int
    new: int = 0
    renumbered: int = 0
    missing: int = 0
    reviews_opened: int = 0
    capability_changes: int = 0
    partial: bool = False
    duration_ms: int = 0

    def __post_init__(self) -> None:
        """Validate the counts.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a count is negative or beyond ``MAX_COUNT``.
        """
        for name in ("interfaces", "new", "renumbered", "missing", "reviews_opened",
                     "capability_changes"):  # fmt: skip
            require_int(getattr(self, name), name, minimum=0, maximum=MAX_COUNT)
        require_int(self.duration_ms, "duration_ms", minimum=0, maximum=MAX_COUNT * 1000)
        if not isinstance(self.partial, bool):
            raise ValidationError("partial must be a bool", field="partial")
