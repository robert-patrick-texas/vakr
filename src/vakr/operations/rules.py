"""Who may have a manual rediscovery, and the small shapes requests use (DISC-003, SEC-018).

``refusal`` says, in operator words, why no collector would rediscover a
device, so the API and the CLI refuse at once instead of queueing an
operation that would only expire. ``clean_actor`` makes any requester name
storable in an operation record.

Dependencies:
    vakr.domain: Devices, operations, enums, errors.

Safety:
    Pure.

Example:
    >>> clean_actor("  cli:alice   (sudo) ")
    'cli:alice (sudo)'
"""

from dataclasses import dataclass

from vakr.domain.enums import AdminState, ReachabilityStrategy
from vakr.domain.errors import DomainError
from vakr.domain.inventory import Device
from vakr.domain.operations import MAX_ACTOR_LENGTH, Operation

DISCOVERED_STATES = frozenset({AdminState.ENABLED, AdminState.MAINTENANCE})


class RefusedError(DomainError):
    """The request cannot become an operation; the message says why and what to do."""


@dataclass(frozen=True, slots=True)
class Requested:
    """The answer to a rediscovery request.

    Attributes:
        operation: The operation to follow.
        created: False when the device already had one queued or running.
    """

    operation: Operation
    created: bool


def clean_actor(actor: str) -> str:
    """Make a requester name storable: one line, trimmed, at most ``MAX_ACTOR_LENGTH``.

    Args:
        actor: Username, ``local:<account>``, or ``cli:<login>``.

    Returns:
        str: The cleaned name; ``unknown`` for an empty one.

    Raises:
        None
    """
    clean = " ".join("".join(c for c in actor if c.isprintable()).split())
    return (clean or "unknown")[:MAX_ACTOR_LENGTH].strip()


def refusal(device: Device, default: ReachabilityStrategy) -> str | None:
    """Say why no collector would rediscover a device, if none would (DISC-003).

    Args:
        device: The device.
        default: ``polling.reachability_strategy``, for devices without an override.

    Returns:
        str | None: The reason in operator words, or ``None`` when it may be rediscovered.

    Raises:
        None
    """
    if device.admin_state not in DISCOVERED_STATES:
        return (f"{device.name} is {device.admin_state.value}; rediscovery runs only for "
                "enabled devices and devices in maintenance")  # fmt: skip
    if device.credential_ref is None:
        return f"{device.name} has no SNMP credential, so there is nothing to discover"
    if (device.reachability or default) is ReachabilityStrategy.ICMP_ONLY:
        return f"{device.name} is polled icmp_only, without SNMP, so there is nothing to discover"
    return None
