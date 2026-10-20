# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the bundle writer: manifest checksums, caps, redaction (FIELD-003, FIELD-004).

The development side trusts the manifest to prove nothing was lost or
altered, so every member's SHA-256 and size must match what is actually in
the archive. The bundle must stay bounded, must never carry the run's SNMP
community, and must be byte-identical for identical input.

Dependencies:
    pytest: Test runner.
    fieldkit_bundle: Module under test.

Safety:
    Writes only beneath ``tmp_path``. The "community" is a made-up string.

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_bundle.py
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import read_bundle
from fieldkit_bundle import MANIFEST, BundleError, BundleWriter, check_member_name, safe_label

WHEN = datetime(2026, 10, 20, 10, 15, tzinfo=UTC)
FAKE_COMMUNITY = b"not-a-real-community-7"


def write_bundle(path: Path, limits: tuple[int, int] = (10**6, 10**5)) -> Path:
    """Write a small bundle with text and JSON members.

    Args:
        path: Destination ``.tar.gz``.
        limits: Total and per-member caps.

    Returns:
        Path: The finished bundle.

    Raises:
        OSError: If writing fails.
    """
    writer = BundleWriter(path, WHEN, limits, secrets=[FAKE_COMMUNITY])
    writer.add_text("host/os-release.txt", "NAME=Ubuntu\n")
    writer.add_json("host/system.json", {"cpu_count": 8})
    writer.add_text("snmp/rtr1/system.txt", "sysContact " + FAKE_COMMUNITY.decode() + "\n")
    return writer.close({"bundle_id": "test-bundle", "profiles": ["host"]})


def test_manifest_lists_sha256_and_size_of_every_member(tmp_path: Path) -> None:
    """Every member except the manifest is listed with its true SHA-256 and size (FIELD-003).

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    members = read_bundle(write_bundle(tmp_path / "b.tar.gz"))
    manifest = json.loads(members.pop(MANIFEST))
    assert set(manifest["members"]) == set(members)
    for name, data in members.items():
        assert manifest["members"][name] == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    assert manifest["profiles"] == ["host"]


def test_credentials_are_redacted_and_noted(tmp_path: Path) -> None:
    """A member containing the community is redacted, and the manifest says so (FIELD-004).

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    path = write_bundle(tmp_path / "b.tar.gz")
    assert FAKE_COMMUNITY not in path.read_bytes()
    members = read_bundle(path)
    assert all(FAKE_COMMUNITY not in data for data in members.values())
    assert b"[REDACTED]" in members["snmp/rtr1/system.txt"]
    assert any("redacted" in n for n in json.loads(members[MANIFEST])["notes"])


def test_bundle_is_private_and_byte_identical_for_identical_input(tmp_path: Path) -> None:
    """Same input and time give the same bytes; the file is created 0600.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    first, second = write_bundle(tmp_path / "a.tar.gz"), write_bundle(tmp_path / "b.tar.gz")
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.partial"))


def test_size_caps_truncate_and_skip(tmp_path: Path) -> None:
    """Oversized members are truncated and members past the total cap are skipped.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    writer = BundleWriter(tmp_path / "b.tar.gz", WHEN, (150, 100))
    writer.add_text("big.txt", "x" * 500)
    writer.add_text("next.txt", "y" * 80)
    members = read_bundle(writer.close({}))
    notes = json.loads(members[MANIFEST])["notes"]
    assert len(members["big.txt"]) == 100
    assert "next.txt" not in members
    assert any("truncated" in n for n in notes)
    assert any("skipped" in n for n in notes)


@pytest.mark.parametrize(
    "name", ["/etc/passwd", "../up", "a/../../b", "a//b", ".hidden", "a\\b", "x y", ""]
)
def test_unsafe_member_names_are_rejected(name: str) -> None:
    """Names that could escape or confuse the extraction directory are refused.

    Args:
        name: Candidate member name.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(BundleError, match="unsafe member name"):
        check_member_name(name)


def test_duplicates_rejected_and_abort_removes_partial(tmp_path: Path) -> None:
    """A duplicate member is an error, and ``abort`` leaves nothing behind.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    writer = BundleWriter(tmp_path / "b.tar.gz", WHEN, (1000, 1000))
    writer.add_text("a.txt", "1")
    with pytest.raises(BundleError, match="duplicate"):
        writer.add_text("a.txt", "2")
    writer.abort()
    assert list(tmp_path.iterdir()) == []


def test_scrubber_hook_sees_kind_and_manifest(tmp_path: Path) -> None:
    """The pseudonymization hook runs on members (with their kind) and the manifest.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    seen: list[str] = []

    def hook(kind: str, text: str) -> str:
        """Record the kind and replace a marker.

        Args:
            kind: Member kind.
            text: Member text.

        Returns:
            str: Text with ``SECRETHOST`` replaced.

        Raises:
            None
        """
        seen.append(kind)
        return text.replace("SECRETHOST", "host-0")

    writer = BundleWriter(tmp_path / "b.tar.gz", WHEN, (1000, 1000), scrubber=hook)
    writer.add_text("snmp/x/system.txt", "SECRETHOST", kind="snmpwalk")
    members = read_bundle(writer.close({"peer": "SECRETHOST"}))
    assert seen == ["snmpwalk", "text"]
    assert all(b"SECRETHOST" not in data for data in members.values())


def test_safe_label_never_starts_with_dot_or_contains_separators() -> None:
    """Device labels are safe path components.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert safe_label("udp6:[2001:db8::1]:161") == "udp6__2001_db8__1__161"
    assert safe_label("../x") == "_x"
    assert safe_label("") == "unknown"
