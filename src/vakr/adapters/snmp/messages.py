"""Operator-language errors for every way an SNMP request can fail.

Each message says what happened and what it does and does not mean,
following blueprint 18.3: a timeout is not proof the device is down, and a
collector-side failure says nothing about the device at all. Messages name
the agent by ``address:port`` and never include a community.

Dependencies:
    vakr.ports.snmp: ``SnmpError`` and the error-status names.

Safety:
    No function here accepts a credential.

Example:
    >>> timeout("192.0.2.1:161", 2.0, 2).message
    'no answer from 192.0.2.1:161 within 2 s on any of 2 attempts'
"""

from vakr.ports.snmp import STATUS_NAMES, SnmpError, SnmpErrorKind, format_oid
from vakr.ports.snmp.errors import TOO_BIG
from vakr.ports.snmp.values import Oid


def timeout(label: str, timeout_s: float, attempts: int) -> SnmpError:
    """Describe a request that got no answer on any attempt.

    Args:
        label: The agent, as ``address:port``.
        timeout_s: Seconds waited per attempt.
        attempts: Attempts made.

    Returns:
        SnmpError: A ``TIMEOUT`` error.

    Raises:
        None
    """
    text = f"no answer from {label} within {timeout_s:g} s on any of {attempts} attempts"
    if attempts == 1:
        text = f"no answer from {label} within {timeout_s:g} s"
    return SnmpError(SnmpErrorKind.TIMEOUT, text)


def abandoned(label: str) -> SnmpError:
    """Describe OIDs not requested because the agent had stopped answering.

    Args:
        label: The agent.

    Returns:
        SnmpError: An ``ABANDONED`` error.

    Raises:
        None
    """
    return SnmpError(
        SnmpErrorKind.ABANDONED,
        f"not requested: {label} stopped answering earlier in this request",
    )


def too_big(label: str, size: int) -> SnmpError:
    """Describe a tooBig answer at the smallest allowed request size.

    Args:
        label: The agent.
        size: Varbinds (or repetitions) in the last request.

    Returns:
        SnmpError: A ``TOO_BIG`` error carrying status 1.

    Raises:
        None
    """
    return SnmpError(
        SnmpErrorKind.TOO_BIG,
        f"{label} answered tooBig even for a request of {size} varbinds",
        status=TOO_BIG,
    )


def agent_error(label: str, status: int, index: int, oid: Oid | None) -> SnmpError:
    """Describe an error-status answer; the device is reachable but refused.

    Args:
        label: The agent.
        status: RFC 3416 error-status.
        index: RFC 3416 error-index.
        oid: The OID the index points to, when it points into the request.

    Returns:
        SnmpError: An ``AGENT_ERROR`` error.

    Raises:
        None
    """
    name = STATUS_NAMES.get(status, f"status {status}")
    where = f" for {format_oid(oid)}" if oid is not None else ""
    text = f"{label} answered with error {name}{where}; the device is reachable but refused"
    return SnmpError(SnmpErrorKind.AGENT_ERROR, text, status=status, index=index or None)


def malformed(label: str, what: str) -> SnmpError:
    """Describe an answer that breaks the SNMP protocol.

    Args:
        label: The agent.
        what: What was wrong, for example ``OIDs out of order``.

    Returns:
        SnmpError: A ``MALFORMED`` error.

    Raises:
        None
    """
    return SnmpError(SnmpErrorKind.MALFORMED, f"{label} sent an unusable answer: {what}")


def walk_limit(label: str, rows: int) -> SnmpError:
    """Describe a walk stopped by the row bound.

    Args:
        label: The agent.
        rows: The bound that was reached.

    Returns:
        SnmpError: A ``LIMIT`` error.

    Raises:
        None
    """
    return SnmpError(
        SnmpErrorKind.LIMIT,
        f"stopped walking {label} after {rows} rows (snmp.max_walk_rows); data may be incomplete",
    )


def collector(kind: SnmpErrorKind, label: str, what: str) -> SnmpError:
    """Describe a collector-side failure, which says nothing about the device.

    Args:
        kind: ``TRANSPORT``, ``CREDENTIAL``, ``RESOLUTION``, or ``UNSUPPORTED``.
        label: The agent.
        what: What failed, without any secret.

    Returns:
        SnmpError: The error, worded so it cannot be read as the device
        being down.

    Raises:
        None
    """
    return SnmpError(kind, f"collector problem, not a device problem: {what} ({label})")
