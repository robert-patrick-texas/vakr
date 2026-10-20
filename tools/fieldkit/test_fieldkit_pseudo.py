# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for pseudonymization of addresses, names, free text, and walks (FIELD-005).

Pseudonyms must be stable across runs (same key, same output), land in the
documented ranges, keep classification keywords (META-012) so the keyword
classifier can still be tested, and keep SNMP walks valid fixtures: IP-MIB
indexes, prefix pointers, and the values they point at must stay consistent.

Dependencies:
    pytest: Test runner.
    fieldkit_pseudo, fieldkit_scrub, fieldkit_mibscrub, fieldkit_crypto: Under test.

Safety:
    Keys are generated per test; the addresses are documentation examples.

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_pseudo.py
"""

import ipaddress
from pathlib import Path

import pytest
from fieldkit_crypto import load_or_create_key
from fieldkit_mibscrub import scrub_walk
from fieldkit_pseudo import Pseudonymizer
from fieldkit_scrub import Scrubber

KEY = bytes(range(32))
KEYWORDS = ("ISP", "WAN", "UPLINK", "TRANSIT")
WALK = """\
.1.3.6.1.2.1.1.1.0 = STRING: Cisco IOS XE Software, Version 17.9.4a
rtr-east-1 serial FOC2341X0AB
.1.3.6.1.2.1.1.3.0 = 148551
.1.3.6.1.2.1.1.4.0 = STRING: noc@corp.example
.1.3.6.1.2.1.1.5.0 = STRING: rtr-east-1.corp.example
.1.3.6.1.2.1.1.6.0 = STRING: "Dallas DC2 row 4"
.1.3.6.1.2.1.2.2.1.6.2 = STRING: 0:1a:2b:3c:4d:5e
.1.3.6.1.2.1.2.2.1.6.3 = Hex-STRING: 00 1A 2B 3C 4D 5F
.1.3.6.1.2.1.31.1.1.1.18.2 = STRING: WAN-ISP_Carrier ckt 77123
.1.3.6.1.2.1.31.1.1.1.18.3 = Hex-STRING: 55 50 4C 49 4E 4B 20 63 6F 72 65
.1.3.6.1.2.1.4.20.1.1.10.20.30.5 = IpAddress: 10.20.30.5
.1.3.6.1.2.1.4.20.1.3.10.20.30.5 = IpAddress: 255.255.255.252
.1.3.6.1.2.1.4.34.1.5.1.4.10.20.30.5 = OID: .1.3.6.1.2.1.4.32.1.5.2.1.4.10.20.30.4.30
.1.3.6.1.2.1.4.34.1.3.2.16.32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.1 = INTEGER: 2
.1.3.6.1.2.1.2.2.1.10.2 = Counter32: 123456
"""


def test_ipv4_is_stable_in_range_and_keyed() -> None:
    """Same key, same pseudonym; another key, another one; always in 198.18/15.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    first, again = Pseudonymizer(KEY).ip("10.1.2.3"), Pseudonymizer(KEY).ip("10.1.2.3")
    other = Pseudonymizer(bytes(32)).ip("10.1.2.3")
    assert first == again
    assert first != other
    assert ipaddress.ip_address(first) in ipaddress.ip_network("198.18.0.0/15")


def test_ipv4_map_preserves_shared_prefixes() -> None:
    """Hosts in one /24 stay in one pseudonymous /24 (prefix preservation).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    p = Pseudonymizer(KEY)
    a, b = (ipaddress.ip_address(p.ip(f"10.9.8.{n}")) for n in (1, 200))
    assert ipaddress.ip_network(f"{a}/24", strict=False) == ipaddress.ip_network(
        f"{b}/24", strict=False
    )


@pytest.mark.parametrize(
    "value",
    ["255.255.255.252", "127.0.0.1", "0.0.0.0", "224.0.0.5"],  # noqa: S104 - data, not a bind
)
def test_structural_ipv4_values_are_kept(value: str) -> None:
    """Masks, loopback, unspecified, and multicast describe structure, not identity.

    Args:
        value: Address that must stay unchanged.

    Returns:
        None

    Raises:
        None
    """
    assert Pseudonymizer(KEY).ip(value) == value


def test_ipv6_maps_to_documentation_prefix_and_link_local_stays_local() -> None:
    """Global IPv6 goes to 2001:db8::/32; link-local stays in fe80::/64.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    p = Pseudonymizer(KEY)
    assert ipaddress.ip_address(p.ip("2600:1f18::10")) in ipaddress.ip_network("2001:db8::/32")
    assert ipaddress.ip_address(p.ip("fe80::21a:2bff:fe3c:4d5e")) in ipaddress.ip_network(
        "fe80::/64"
    )
    assert p.ip("::1") == "::1"


def test_mac_is_locally_administered_unicast_and_stable() -> None:
    """MAC pseudonyms set the U/L bit, clear the I/G bit, and repeat exactly.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    raw = bytes.fromhex("001a2b3c4d5e")
    mapped = Pseudonymizer(KEY).mac(raw)
    assert mapped == Pseudonymizer(KEY).mac(raw)
    assert mapped[0] & 0x02
    assert not mapped[0] & 0x01
    assert Pseudonymizer(KEY).mac(b"\xff" * 6) == b"\xff" * 6


def test_hostnames_and_tokens_are_case_insensitive() -> None:
    """``RTR1`` and ``rtr1`` are one device; pseudonyms look like ``host-<8 hex>``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    p = Pseudonymizer(KEY)
    assert p.hostname("RTR1") == p.hostname("rtr1")
    assert p.hostname("rtr1").startswith("host-")
    assert len(p.hostname("rtr1")) == len("host-") + 8
    assert p.hostname("10.0.0.1") == p.ip("10.0.0.1")


def test_free_text_keeps_keywords_on_whole_tokens() -> None:
    """Keywords survive verbatim on token boundaries; ``SWANSEA`` is not ``WAN`` (META-012).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    p = Pseudonymizer(KEY, (*KEYWORDS, "MPLS"))
    out = p.free_text("WAN-ISP_Carrier SWANSEA transit mpls ckt 42")
    assert out.startswith("WAN-ISP_")
    assert "transit" in out
    assert "mpls" in out
    for secret_token in ("Carrier", "SWANSEA", "ckt", "42"):
        assert secret_token not in out
    assert out == Pseudonymizer(KEY, (*KEYWORDS, "MPLS")).free_text(
        "WAN-ISP_Carrier SWANSEA transit mpls ckt 42"
    )


def test_ipv4_collisions_probe_to_a_free_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two originals never share a pseudonym, even when the /15 fold collides.

    Args:
        monkeypatch: Forces every address onto the same folded value.

    Returns:
        None

    Raises:
        None
    """
    p = Pseudonymizer(KEY)
    monkeypatch.setattr(p, "_prefix_preserving", lambda *_: 7)
    assert p.ip("10.0.0.1") != p.ip("10.0.0.2")
    assert p.collisions == 1


def test_scrub_text_replaces_names_and_addresses_but_not_oids() -> None:
    """Generic scrubbing leaves OIDs, versions, and times alone.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    s = Scrubber(Pseudonymizer(KEY))
    s.add_name("rtr-east-1.corp.example")
    s.add_name("10.0.0.9")  # IP literals are handled by the address rules instead
    text = (
        "PING rtr-east-1.corp.example (10.0.0.9) at 12:30:45 via 00-1A-2B-3C-4D-5E and "
        "001a.2b3c.4d5e, OID .1.3.6.1.2.1.1 OpenSSL 3.5.5 Reference ID : 0A000009 (x)"
    )
    out = s.text(text)
    for leaked in ("rtr-east-1", "10.0.0.9", "00-1A-2B-3C-4D-5E", "001a.2b3c.4d5e", "0A000009"):
        assert leaked not in out
    for kept in ("12:30:45", ".1.3.6.1.2.1.1", "OpenSSL 3.5.5"):
        assert kept in out


def test_scrub_walk_keeps_fixtures_valid_and_keywords() -> None:
    """IP-MIB indexes, values, and prefix pointers are rewritten consistently.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    s = Scrubber(Pseudonymizer(KEY, KEYWORDS))
    out = scrub_walk(s, WALK)
    mapped = s.p.ip("10.20.30.5")
    assert f".1.3.6.1.2.1.4.20.1.1.{mapped} = IpAddress: {mapped}" in out
    assert f".1.3.6.1.2.1.4.20.1.3.{mapped} = IpAddress: 255.255.255.252" in out
    network = ipaddress.ip_network(f"{mapped}/30", strict=False).network_address
    assert f"= OID: .1.3.6.1.2.1.4.32.1.5.2.1.4.{network}.30" in out
    assert ".1.3.6.1.2.1.4.34.1.3.2.16.32.1.13.184." in out  # 2001:db8:: stays in range
    assert "STRING: WAN-ISP_" in out
    assert "STRING: UPLINK " in out  # decoded from Hex-STRING, keyword kept
    assert "Counter32: 123456" in out
    assert ".1.3.6.1.2.1.1.3.0 = 148551" in out
    assert "Version 17.9.4a" in out
    for leaked in ("10.20.30", "rtr-east-1", "corp.example", "Dallas", "77123", "FOC2341X0AB"):
        assert leaked not in out
    assert "0:1a:2b:3c:4d:5e" not in out
    assert "00 1A 2B 3C 4D 5F" not in out
    assert out.count("\n.1.3.6.1") == WALK.count("\n.1.3.6.1")


def test_scrub_walk_maps_raw_printable_mac() -> None:
    """Without MIBs a MAC can print as six raw characters; it is still mapped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    s = Scrubber(Pseudonymizer(KEY))
    out = scrub_walk(s, '.1.3.6.1.2.1.2.2.1.6.9 = STRING: "ABCDEF"\n')
    assert "ABCDEF" not in out
    assert "= Hex-STRING: " in out


def test_pseudonym_key_is_created_private_and_reused(tmp_path: Path) -> None:
    """The key is created 0600 in a 0700 directory and reused on the next run.

    Args:
        tmp_path: Per-test directory standing in for ``~/.config``.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "cfg" / "vakr-fieldkit" / "pseudonym.key"
    key = load_or_create_key(path)
    assert load_or_create_key(path) == key
    assert len(key) == 32
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_pseudonym_key_refused_when_readable_or_malformed(tmp_path: Path) -> None:
    """A key readable by others, or a malformed one, is refused.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "pseudonym.key"
    path.write_text("00" * 32 + "\n", encoding="ascii")
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="0600"):
        load_or_create_key(path)
    path.chmod(0o600)
    path.write_text("abcd\n", encoding="ascii")
    with pytest.raises(ValueError, match="32-byte"):
        load_or_create_key(path)
