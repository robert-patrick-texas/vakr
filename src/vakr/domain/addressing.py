"""Validation of poll addresses, interface addresses, and MAC strings.

A device is polled at an IP literal or a DNS name (blueprint 9.11). Storing
only canonical forms means two records for the same target compare equal and
every backend indexes the same text, so ``2001:DB8::1`` and ``2001:db8::1``
cannot become two devices. Normalising operator input into these forms is
the configuration layer's job; the domain only accepts the result.

Dependencies:
    ipaddress: IP literal and interface parsing.
    vakr.domain.errors: ``ValidationError``.

Safety:
    Pure string checks; nothing here resolves names or touches the network.

Example:
    >>> require_poll_address("core1.example.net", None)
    >>> require_interface_addresses(("10.0.0.1/30", "2001:db8::1/64"), "addresses")
    >>> require_mac("00:1a:2b:3c:4d:5e", "mac")
"""

import ipaddress
import re

from vakr.domain.enums import AddressFamily
from vakr.domain.errors import ValidationError

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*\.?")
_MAX_DNS_NAME = 253  # RFC 1035 limit on a presentation-format name
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2})*")


def require_poll_address(address: object, family: AddressFamily | None) -> None:
    """Require a canonical IP literal or an RFC 1123 host name.

    Args:
        address: Candidate poll address.
        family: Preferred address family, if the device sets one. An IP
            literal must belong to it.

    Returns:
        None

    Raises:
        ValidationError: If the address is neither a canonical IP literal nor
            a valid host name, or an IP literal contradicts ``family``.
    """
    if not isinstance(address, str) or not address:
        raise ValidationError("poll_address must be a non-empty string", field="poll_address")
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        _require_hostname(address)
        return
    if str(ip) != address:
        raise ValidationError(
            f"poll_address must be written canonically as {ip}", field="poll_address"
        )
    actual = AddressFamily.IPV4 if ip.version == 4 else AddressFamily.IPV6
    if family is not None and family is not actual:
        raise ValidationError(
            f"poll_address {address} is {actual}, but address_family is {family}",
            field="poll_address",
        )


def _require_hostname(name: str) -> None:
    """Require an RFC 1123 host name whose last label is not all digits.

    An all-numeric last label such as ``10.0.0`` looks like a mistyped IPv4
    address, so it is refused rather than sent to DNS.

    Args:
        name: Candidate host name.

    Returns:
        None

    Raises:
        ValidationError: If ``name`` is not a valid host name.
    """
    last = name.rstrip(".").rsplit(".", 1)[-1]
    if len(name) > _MAX_DNS_NAME or not _HOSTNAME.fullmatch(name) or last.isdigit():
        raise ValidationError(
            f"poll_address {name!r} is neither an IP address nor a valid host name",
            field="poll_address",
        )


def require_interface_addresses(values: object, field: str) -> None:
    """Require a tuple of distinct canonical interface addresses such as ``10.0.0.1/30``.

    Args:
        values: Candidate addresses.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``values`` is not a tuple of distinct strings in
            the canonical ``address/prefix`` form.
    """
    if not isinstance(values, tuple):
        raise ValidationError(f"{field} must be a tuple of strings", field=field)
    for value in values:
        try:
            canonical = str(ipaddress.ip_interface(value))
        except TypeError, ValueError:
            message = f"{field} has an invalid address {value!r}"
            raise ValidationError(message, field=field) from None
        if canonical != value:
            raise ValidationError(f"{field} must write {value} as {canonical}", field=field)
    if len(set(values)) != len(values):
        raise ValidationError(f"{field} must not repeat an address", field=field)


def require_mac(value: object, field: str) -> None:
    """Require ``None`` or lower-case colon-separated hex octets.

    ``ifPhysAddress`` is not always six octets (some interfaces report none,
    others eight or twenty), so only the notation is checked here.

    Args:
        value: Candidate physical address.
        field: Field name for the error message.

    Returns:
        None

    Raises:
        ValidationError: If ``value`` is not ``None`` or ``aa:bb:...`` text.
    """
    if value is not None and (not isinstance(value, str) or not _MAC.fullmatch(value)):
        raise ValidationError(f"{field} must be lower-case colon-separated hex octets", field=field)
