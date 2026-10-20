"""The request budget for one SNMP session (SNMP-011, SNMP-017, SNMP-018).

``SnmpSettings`` holds the timeout, complete-attempt retries, varbinds per
request, GETBULK repetitions, and the bounds that keep a broken agent from
holding a poll forever. Defaults come from Appendix E.4, E.9, and E.23;
operators set them under ``[snmp]`` in the configuration.

Dependencies:
    math: Finiteness check.
    vakr.domain.errors: ``ValidationError``.

Safety:
    Every limit is bounded, so no setting can make a request unbounded.

Example:
    >>> SnmpSettings().attempts, SnmpSettings(retries=0).attempts
    (2, 1)
"""

import math
from dataclasses import dataclass

from vakr.domain.errors import ValidationError

DEFAULT_TIMEOUT_S = 2.0  # E.9, SNMP-011
DEFAULT_RETRIES = 1  # E.9, SNMP-011
DEFAULT_MAX_VARBINDS = 30  # E.4: start at 30 varbinds per PDU
MIN_VARBINDS = 5  # E.4: halving after tooBig stops at 5
DEFAULT_MAX_REPETITIONS = 25  # E.9: discovery GETBULK max-repetitions
DEFAULT_MAX_WALK_ROWS = 10_000  # E.23: varbinds one subtree walk may return
DEFAULT_ABANDON_AFTER = 2  # E.23: consecutive unanswered requests before giving up
MAX_TIMEOUT_S = 30.0  # E.18
MAX_RETRIES = 5  # E.18
MAX_VARBINDS_LIMIT = 100  # E.23
MAX_REPETITIONS_LIMIT = 100  # E.23
MAX_WALK_ROWS_LIMIT = 100_000  # E.23
MAX_ABANDON_AFTER = 10  # E.23


def require_int(value: object, field: str, low: int, high: int) -> None:
    """Require an integer (not a bool) within ``[low, high]``.

    Args:
        value: Candidate value.
        field: Field name for the error message.
        low: Smallest allowed value.
        high: Largest allowed value.

    Returns:
        None

    Raises:
        ValidationError: If the value is not an int in range.
    """
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{field} must be an integer from {low} to {high}", field=field)


@dataclass(frozen=True, slots=True)
class SnmpSettings:
    """The request budget for one session (SNMP-011, SNMP-017, SNMP-018).

    Attributes:
        timeout_s: Seconds to wait for each attempt's response.
        retries: Extra complete attempts after a timeout.
        max_varbinds: Varbinds per request at the start of a session.
        min_varbinds: Floor for halving after tooBig or size-related timeouts.
        max_repetitions: GETBULK max-repetitions for subtree walks.
        max_walk_rows: Most varbinds one subtree walk may return.
        abandon_after: Consecutive unanswered requests after which the rest
            of the same request is abandoned instead of sent.
    """

    timeout_s: float = DEFAULT_TIMEOUT_S
    retries: int = DEFAULT_RETRIES
    max_varbinds: int = DEFAULT_MAX_VARBINDS
    min_varbinds: int = MIN_VARBINDS
    max_repetitions: int = DEFAULT_MAX_REPETITIONS
    max_walk_rows: int = DEFAULT_MAX_WALK_ROWS
    abandon_after: int = DEFAULT_ABANDON_AFTER

    def __post_init__(self) -> None:
        """Validate the budget.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a value is out of range or the varbind floor
                exceeds the starting size.
        """
        timeout = self.timeout_s
        if type(timeout) not in (int, float) or not math.isfinite(timeout):
            raise ValidationError("timeout_s must be a number", field="timeout_s")
        if not 0 < timeout <= MAX_TIMEOUT_S:
            raise ValidationError("timeout_s must be above 0 and at most 30", field="timeout_s")
        require_int(self.retries, "retries", 0, MAX_RETRIES)
        require_int(self.max_varbinds, "max_varbinds", 1, MAX_VARBINDS_LIMIT)
        require_int(self.min_varbinds, "min_varbinds", 1, self.max_varbinds)
        require_int(self.max_repetitions, "max_repetitions", 1, MAX_REPETITIONS_LIMIT)
        require_int(self.max_walk_rows, "max_walk_rows", 1, MAX_WALK_ROWS_LIMIT)
        require_int(self.abandon_after, "abandon_after", 1, MAX_ABANDON_AFTER)

    @property
    def attempts(self) -> int:
        """Return the complete attempts each request may use.

        Args:
            None

        Returns:
            int: ``1 + retries``.

        Raises:
            None
        """
        return 1 + self.retries
