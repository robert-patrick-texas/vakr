"""Explicit SNMP errors: what failed, in operator language, and on which side.

An SNMP exchange can fail in ways that mean very different things to an
operator. A timeout says nothing came back. An agent error status such as
``genErr`` says the device answered but refused. A local socket, DNS, or
credential failure says the *collector* is in trouble, and must never be
reported as the device being down (invariants 2 and 3). ``SnmpError`` names
the kind, and ``collector_side`` tells the two apart.

Dependencies:
    dataclasses, enum: Immutable errors and kinds.

Safety:
    Messages are written by vakr and never include a community or any
    other secret.

Example:
    >>> SnmpError(SnmpErrorKind.AGENT_ERROR, "refused", status=5).code
    'agent_error.genErr'
    >>> SnmpError(SnmpErrorKind.CREDENTIAL, "missing").collector_side
    True
"""

from dataclasses import dataclass
from enum import StrEnum

STATUS_NAMES = {
    0: "noError",
    1: "tooBig",
    2: "noSuchName",
    3: "badValue",
    4: "readOnly",
    5: "genErr",
    6: "noAccess",
    7: "wrongType",
    8: "wrongLength",
    9: "wrongEncoding",
    10: "wrongValue",
    11: "noCreation",
    12: "inconsistentValue",
    13: "resourceUnavailable",
    14: "commitFailed",
    15: "undoFailed",
    16: "authorizationError",
    17: "notWritable",
    18: "inconsistentName",
}
"""RFC 3416 error-status names, for messages and stable error codes."""
TOO_BIG = 1


class SnmpErrorKind(StrEnum):
    """Why part or all of a request produced no data."""

    TIMEOUT = "timeout"  # no response after every attempt
    TOO_BIG = "too_big"  # tooBig even at the smallest request size
    AGENT_ERROR = "agent_error"  # the agent answered with an error status
    MALFORMED = "malformed"  # the answer broke the protocol, e.g. a walk went backwards
    LIMIT = "limit"  # a configured bound stopped a walk
    ABANDONED = "abandoned"  # not sent: the agent stopped answering earlier in the request
    RESOLUTION = "resolution"  # the DNS name did not resolve (9.11)
    CREDENTIAL = "credential"  # the credential could not be resolved
    UNSUPPORTED = "unsupported"  # e.g. SNMPv3, which the MVP does not poll
    TRANSPORT = "transport"  # the collector could not open or use its socket


COLLECTOR_SIDE = frozenset(
    {
        SnmpErrorKind.RESOLUTION,
        SnmpErrorKind.CREDENTIAL,
        SnmpErrorKind.UNSUPPORTED,
        SnmpErrorKind.TRANSPORT,
    }
)
"""Failures before any request reached the agent: never evidence about the target."""


@dataclass(frozen=True, slots=True)
class SnmpError:
    """An explicit SNMP failure, in operator language.

    Attributes:
        kind: The failure class.
        message: What happened and what it means; never contains a secret.
        status: RFC 3416 error-status for ``AGENT_ERROR`` and ``TOO_BIG``.
        index: RFC 3416 error-index (1-based) when the agent gave one.
    """

    kind: SnmpErrorKind
    message: str
    status: int | None = None
    index: int | None = None

    @property
    def collector_side(self) -> bool:
        """Whether the failure lies with the collector, not the target (invariant 2).

        Args:
            None

        Returns:
            bool: True for resolution, credential, unsupported, and
            transport failures.

        Raises:
            None
        """
        return self.kind in COLLECTOR_SIDE

    @property
    def code(self) -> str:
        """Return a stable error code, such as ``timeout`` or ``agent_error.genErr``.

        Args:
            None

        Returns:
            str: The kind, plus the error-status name when there is one.

        Raises:
            None
        """
        if self.status is None or self.kind is not SnmpErrorKind.AGENT_ERROR:
            return self.kind.value
        return f"{self.kind.value}.{STATUS_NAMES.get(self.status, str(self.status))}"


class SnmpSessionError(Exception):
    """A session could not be opened; carries the explicit ``SnmpError``.

    Attributes:
        error: Why the session could not be opened.
    """

    def __init__(self, error: SnmpError) -> None:
        """Wrap the error.

        Args:
            error: Why the session could not be opened.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(error.message)
        self.error = error
