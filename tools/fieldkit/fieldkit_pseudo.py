# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Keyed, consistent pseudonyms for addresses, names, and free text (FIELD-005).

Captures from production become simulator fixtures and test data on the
development side, but the customer's addressing plan, device names, and
circuit descriptions should not leave the site. ``--pseudonymize`` replaces
them with HMAC-SHA256 pseudonyms whose key never leaves the production host,
so the same router or address gets the same pseudonym in every run:

* IPv4 maps into the benchmarking range 198.18.0.0/15 and IPv6 into the
  documentation prefix 2001:db8::/32 (link-local stays in fe80::/64). The map
  is prefix-preserving (Crypto-PAn style), so addresses that share a subnet
  still share one, which keeps ``ipAddressPrefix`` pointers coherent.
* MAC addresses become locally administered unicast addresses.
* Host names become ``host-<8 hex>``.
* Free text keeps classification keywords (META-012) and replaces every
  other token, so the description-keyword classifier can still be tested.

Dependencies:
    hmac, hashlib, ipaddress: Standard library only. The key comes from
        ``fieldkit_crypto.load_or_create_key``.

Safety:
    The key never enters the bundle. Within one run, an IPv4 collision in
    the /15 is resolved by probing the next free address and counted in
    ``collisions``; such an address can get a different pseudonym in a run
    whose address set differs.

Example:
    >>> p = Pseudonymizer(bytes(32), keep_keywords=("WAN",))
    >>> p.ip("10.1.2.3") == p.ip("10.1.2.3"), p.ip("10.1.2.3").startswith("198.1")
    (True, True)
    >>> p.free_text("WAN-ISP_Carrier ckt 42").split("-")[0]
    'WAN'
"""

import hashlib
import hmac
import ipaddress
import re
from collections.abc import Iterable

V4_BASE, V4_BITS = int(ipaddress.IPv4Address("198.18.0.0")), 17
V6_BASE, V6_BITS = int(ipaddress.IPv6Address("2001:db8::")), 96
V6_LINK_LOCAL = int(ipaddress.IPv6Address("fe80::"))
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def is_ip(text: str) -> bool:
    """Report whether ``text`` is an IPv4 or IPv6 literal.

    Args:
        text: Candidate address.

    Returns:
        bool: ``True`` for a valid address literal.

    Raises:
        None
    """
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def is_kept_ipv4(addr: ipaddress.IPv4Address) -> bool:
    """Report whether an IPv4 value is structural rather than identifying.

    Netmasks such as ``255.255.255.252``, loopback, link-local, multicast,
    and reserved values describe structure; rewriting them would make
    ``ipAdEntNetMask`` and similar columns invalid in fixtures.

    Args:
        addr: Parsed address.

    Returns:
        bool: ``True`` when the value is kept verbatim.

    Raises:
        None
    """
    special = addr.is_loopback or addr.is_link_local or addr.is_multicast
    return special or addr.is_reserved or addr.packed[0] in (0, 255)


class Pseudonymizer:
    """Deterministic keyed pseudonyms; one instance per collection run.

    Mappings are cached, so a repeated value costs one HMAC chain. Not
    thread-safe.

    Attributes:
        collisions: IPv4 pseudonym collisions resolved during this run.
    """

    def __init__(self, key: bytes, keep_keywords: Iterable[str] = ()) -> None:
        """Create a pseudonymizer.

        Args:
            key: Secret HMAC key that stays on the production host.
            keep_keywords: Tokens kept verbatim in free text (case-insensitive).

        Returns:
            None

        Raises:
            None
        """
        self._key = key
        self._keep = {k.upper() for k in keep_keywords}
        self._v4: dict[int, int] = {}
        self._v4_used: set[int] = set()
        self._v6: dict[int, int] = {}
        self.collisions = 0

    def _prf(self, label: str, data: str) -> bytes:
        """Return HMAC-SHA256 of ``label`` and ``data`` under the run key.

        Args:
            label: Domain separator such as ``"host"`` or ``"v4"``.
            data: Value being pseudonymized.

        Returns:
            bytes: 32-byte digest.

        Raises:
            None
        """
        return hmac.new(self._key, f"{label}\0{data}".encode(), hashlib.sha256).digest()

    def _prefix_preserving(self, label: str, value: int, width: int) -> int:
        """Permute ``value`` so values sharing a k-bit prefix still share one.

        Args:
            label: Domain separator for this address family.
            value: Address as an integer.
            width: Address width in bits.

        Returns:
            int: The permuted address.

        Raises:
            None
        """
        out = 0
        for i in range(width):
            flip = self._prf(label, f"{i}/{value >> (width - i)}")[0] & 1
            out = (out << 1) | (((value >> (width - 1 - i)) & 1) ^ flip)
        return out

    def ipv4_int(self, value: int) -> int:
        """Map an IPv4 address integer into 198.18.0.0/15.

        Args:
            value: Original address.

        Returns:
            int: Pseudonymous address, or ``value`` if it is structural.

        Raises:
            None
        """
        if is_kept_ipv4(ipaddress.IPv4Address(value)):
            return value
        if value not in self._v4:
            low = self._prefix_preserving("v4", value, 32) & ((1 << V4_BITS) - 1)
            while V4_BASE | low in self._v4_used:
                self.collisions += 1
                low = (low + 1) & ((1 << V4_BITS) - 1)
            self._v4[value] = V4_BASE | low
            self._v4_used.add(V4_BASE | low)
        return self._v4[value]

    def ipv6_int(self, value: int) -> int:
        """Map an IPv6 address integer into 2001:db8::/32 (fe80::/64 if link-local).

        Args:
            value: Original address.

        Returns:
            int: Pseudonymous address, or ``value`` if it is structural.

        Raises:
            None
        """
        addr = ipaddress.IPv6Address(value)
        if addr.is_unspecified or addr.is_loopback or addr.is_multicast:
            return value
        if value not in self._v6:
            mixed = self._prefix_preserving("v6", value, 128)
            if addr.is_link_local:
                self._v6[value] = V6_LINK_LOCAL | (mixed & ((1 << 64) - 1))
            else:
                self._v6[value] = V6_BASE | (mixed & ((1 << V6_BITS) - 1))
        return self._v6[value]

    def ip(self, text: str) -> str:
        """Map IPv4 or IPv6 text; anything else is returned unchanged.

        Args:
            text: Candidate address.

        Returns:
            str: Pseudonymous address text (IPv6 in compressed form).

        Raises:
            None
        """
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            return text
        if isinstance(addr, ipaddress.IPv4Address):
            return str(ipaddress.IPv4Address(self.ipv4_int(int(addr))))
        return str(ipaddress.IPv6Address(self.ipv6_int(int(addr))))

    def mac(self, raw: bytes) -> bytes:
        """Map a MAC address to a locally administered unicast address.

        Args:
            raw: Six address bytes.

        Returns:
            bytes: Pseudonymous six bytes; all-zero and broadcast are kept.

        Raises:
            None
        """
        if raw in (bytes(6), b"\xff" * 6) or len(raw) != 6:
            return raw
        digest = self._prf("mac", raw.hex())
        # Set the locally-administered bit and clear the multicast bit (IEEE 802).
        return bytes([(digest[0] & 0xFC) | 0x02]) + digest[1:6]

    def hostname(self, name: str) -> str:
        """Map a host name (case-insensitive) to ``host-<8 hex>``.

        Args:
            name: Original host name or FQDN.

        Returns:
            str: Pseudonym, or an address pseudonym if ``name`` is an IP literal.

        Raises:
            None
        """
        if is_ip(name):
            return self.ip(name)
        return "host-" + self._prf("host", name.strip().lower()).hex()[:8]

    def token(self, word: str) -> str:
        """Replace one free-text token unless it is a classification keyword.

        Args:
            word: Alphanumeric token.

        Returns:
            str: ``word`` if it is a keyword, else ``x`` plus 6 hex digits.

        Raises:
            None
        """
        if word.upper() in self._keep:
            return word
        return "x" + self._prf("tok", word.lower()).hex()[:6]

    def free_text(self, text: str) -> str:
        """Pseudonymize free text such as ``ifAlias`` or ``sysLocation``.

        Tokens are split on every non-alphanumeric character, the rule the
        keyword classifier uses (META-012), and separators are kept.

        Args:
            text: Original text.

        Returns:
            str: Text with every non-keyword token replaced.

        Raises:
            None
        """
        return _TOKEN_RE.sub(lambda m: self.token(m.group(0)), text)
