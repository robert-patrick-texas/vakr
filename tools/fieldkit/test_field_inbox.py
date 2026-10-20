# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the development-side inbox: safe extraction and processing (FIELD-007, FIELD-008).

A bundle is data from another site. Extraction must refuse absolute paths,
``..`` components, links, and devices, must verify every checksum against
the manifest before anything appears under ``inbox/``, and must leave a
failed bundle where it was. The full path (decrypt, verify, extract, move to
``processed/``) is exercised with a throwaway recipient key pair.

Dependencies:
    pytest: Test runner.
    field_inbox, field_inbox_extract: Modules under test.
    fieldkit_bundle, fieldkit_crypto: Build and encrypt real bundles.

Safety:
    Every key is generated per session by ``conftest.pki``; nothing touches
    the real ``~/vakr-inbox`` or recipient key.

Example:
    $ uv run pytest tools/fieldkit/test_field_inbox.py
"""

import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import field_inbox
import pytest
from conftest import Pki
from field_inbox_extract import UnsafeBundleError, extract_verified
from fieldkit_bundle import BundleWriter
from fieldkit_crypto import encrypt

WHEN = datetime(2026, 10, 20, 10, 15, tzinfo=UTC)
LIMIT = 10**7


def good_bundle(path: Path, bundle_id: str = "vakr-field-east1-20261020T101500Z") -> Path:
    """Write a valid bundle with the real writer.

    Args:
        path: Destination ``.tar.gz``.
        bundle_id: Manifest bundle id.

    Returns:
        Path: The bundle.

    Raises:
        OSError: If writing fails.
    """
    writer = BundleWriter(path, WHEN, (LIMIT, LIMIT))
    writer.add_text("host/os-release.txt", "NAME=Ubuntu\n")
    writer.add_text("snmp/host-1/system.txt", ".1.3.6.1.2.1.1.5.0 = STRING: host-1\n")
    return writer.close({"bundle_id": bundle_id, "profiles": ["host", "snmp"]})


def evil_bundle(path: Path, attrs: dict[str, Any]) -> Path:
    """Write a bundle with one crafted member next to an otherwise valid manifest.

    Args:
        path: Destination ``.tar.gz``.
        attrs: TarInfo attributes that make the member hostile.

    Returns:
        Path: The bundle.

    Raises:
        OSError: If writing fails.
    """
    manifest = {"bundle_id": "evil", "members": {}}
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("payload")
        for key, value in attrs.items():
            setattr(info, key, value)
        tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isreg() else None)
        raw = json.dumps(manifest).encode()
        meta = tarfile.TarInfo("manifest.json")
        meta.size = len(raw)
        tar.addfile(meta, io.BytesIO(raw))
    return path


HOSTILE = [
    ({"name": "/tmp/owned", "size": 1}, "absolute path"),  # noqa: S108 - the attack under test
    ({"name": "snmp/../../owned", "size": 1}, "'..'"),
    ({"type": tarfile.SYMTYPE, "linkname": "/etc/passwd"}, "links are not allowed"),
    ({"type": tarfile.LNKTYPE, "linkname": "manifest.json"}, "links are not allowed"),
    ({"type": tarfile.CHRTYPE, "devmajor": 1, "devminor": 3}, "device or FIFO"),
    ({"type": tarfile.FIFOTYPE}, "device or FIFO"),
    ({"type": tarfile.DIRTYPE}, "only regular files"),
]


@pytest.mark.parametrize(("attrs", "reason"), HOSTILE)
def test_unsafe_members_are_rejected_and_nothing_is_extracted(
    tmp_path: Path, attrs: dict[str, Any], reason: str
) -> None:
    """Hostile members abort extraction and leave ``inbox/`` empty (FIELD-008).

    Args:
        tmp_path: Per-test directory.
        attrs: Attributes of the hostile member.
        reason: Expected rejection text.

    Returns:
        None

    Raises:
        None
    """
    dest = tmp_path / "inbox"
    with pytest.raises(UnsafeBundleError, match=reason):
        extract_verified(evil_bundle(tmp_path / "evil.tar.gz", attrs), dest, LIMIT)
    assert list(dest.iterdir()) == []
    assert not Path("/tmp/owned").exists()  # noqa: S108 - proves the absolute path was not written


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    """A member whose bytes differ from its manifest SHA-256 is refused.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    manifest = {"bundle_id": "b", "members": {"a.txt": {"sha256": "0" * 64, "bytes": 1}}}
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (("a.txt", b"x"), ("manifest.json", json.dumps(manifest).encode())):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(UnsafeBundleError, match="SHA-256 or size differs"):
        extract_verified(path, tmp_path / "inbox", LIMIT)
    assert list((tmp_path / "inbox").iterdir()) == []


def test_missing_manifest_and_unlisted_members_are_rejected(tmp_path: Path) -> None:
    """No manifest, or a member the manifest does not list, is refused.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "nomanifest.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(UnsafeBundleError, match=r"manifest\.json is missing"):
        extract_verified(path, tmp_path / "inbox", LIMIT)
    extra = evil_bundle(tmp_path / "extra.tar.gz", {"size": 1})
    with pytest.raises(UnsafeBundleError, match="unlisted"):
        extract_verified(extra, tmp_path / "inbox", LIMIT)


def test_valid_bundle_extracts_once(tmp_path: Path) -> None:
    """A good bundle lands in ``inbox/<bundle-id>/``; a second copy is refused.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    bundle = good_bundle(tmp_path / "b.tar.gz")
    dest, manifest = extract_verified(bundle, tmp_path / "inbox", LIMIT)
    assert dest == tmp_path / "inbox" / manifest["bundle_id"]
    assert (dest / "snmp/host-1/system.txt").read_text().startswith(".1.3.6.1")
    with pytest.raises(UnsafeBundleError, match="already exists"):
        extract_verified(bundle, tmp_path / "inbox", LIMIT)
    with pytest.raises(UnsafeBundleError, match="larger than"):
        extract_verified(good_bundle(tmp_path / "c.tar.gz", "other"), tmp_path / "inbox", 10)


def _args(tmp_path: Path, pki: Pki, *command: str) -> list[str]:
    """Build field_inbox arguments that point every path into ``tmp_path``.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.
        *command: Subcommand and its arguments.

    Returns:
        list[str]: Arguments for ``field_inbox.main``.

    Raises:
        None
    """
    return ["--recipient", str(pki.recipient), "--key", str(pki.recipient_key),
            "--inbox-dir", str(tmp_path / "box"), "--dest", str(tmp_path / "inbox"),
            *command]  # fmt: skip


def test_process_decrypts_verifies_extracts_and_files_away(
    tmp_path: Path, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """``process`` handles everything in the inbox and moves it to ``processed/`` (FIELD-008).

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.
        capsys: Captures the summary.

    Returns:
        None

    Raises:
        None
    """
    box = tmp_path / "box"
    box.mkdir()
    pki.recipient_key.chmod(0o600)
    plain = good_bundle(tmp_path / "vakr-field-east1-20261020T101500Z.tar.gz")
    encrypt("openssl", pki.recipient, plain, box / (plain.name + ".p7m"))
    assert field_inbox.main(_args(tmp_path, pki, "process")) == 0
    out = capsys.readouterr().out
    assert "vakr-field-east1-20261020T101500Z" in out
    assert "SNMP devices: 1" in out
    assert (box / "processed" / (plain.name + ".p7m")).is_file()
    assert (tmp_path / "inbox/vakr-field-east1-20261020T101500Z/host/os-release.txt").is_file()
    assert field_inbox.main(_args(tmp_path, pki, "process")) == 0
    assert "nothing to process" in capsys.readouterr().out


def test_process_leaves_undecryptable_bundle_in_place(
    tmp_path: Path, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bundle for another recipient fails cleanly and stays in the inbox.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.
        capsys: Captures the error.

    Returns:
        None

    Raises:
        None
    """
    box = tmp_path / "box"
    box.mkdir()
    pki.recipient_key.chmod(0o600)
    plain = good_bundle(tmp_path / "x.tar.gz")
    encrypt("openssl", pki.other, plain, box / "x.tar.gz.p7m")
    assert field_inbox.main(_args(tmp_path, pki, "process")) == 1
    assert "decryption failed" in capsys.readouterr().err
    assert (box / "x.tar.gz.p7m").is_file()
    assert not (tmp_path / "inbox").exists() or not any((tmp_path / "inbox").iterdir())


def test_fetch_refuses_plain_http_and_odd_names(
    tmp_path: Path, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only HTTPS URLs ending in a ``*.p7m`` name are fetched (FIELD-007).

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.
        capsys: Captures errors.

    Returns:
        None

    Raises:
        None
    """
    assert field_inbox.main(_args(tmp_path, pki, "fetch", "http://example.net/b.p7m")) == 1
    assert "only https" in capsys.readouterr().err
    assert field_inbox.main(_args(tmp_path, pki, "fetch", "https://example.net/index.html")) == 1
    assert "not a *.p7m" in capsys.readouterr().err


def test_fetch_downloads_over_verified_https(
    tmp_path: Path, pki: Pki, tls_server: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """``fetch`` saves the body into the inbox, trusting ``--ca-file``.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.
        tls_server: Port of the loopback TLS server.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    url = f"https://localhost:{tls_server}/bundles/b.tar.gz.p7m"
    code = field_inbox.main(_args(tmp_path, pki, "fetch", url, "--ca-file", str(pki.ca)))
    assert code == 0, capsys.readouterr()
    assert (tmp_path / "box" / "b.tar.gz.p7m").read_bytes().startswith(b'{"tag_name"')


def test_init_recipient_creates_private_key_once(tmp_path: Path) -> None:
    """``init-recipient`` makes a 0600 key in a 0700 directory and never replaces it.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    key = tmp_path / "cfg" / "vakr-field" / "recipient-key.pem"
    args = ["--recipient", str(tmp_path / "r" / "dev.pem"), "--key", str(key),
            "--inbox-dir", str(tmp_path / "box"), "--dest", str(tmp_path / "inbox"),
            "init-recipient"]  # fmt: skip
    assert field_inbox.main(args) == 0
    original = key.read_bytes()
    assert key.stat().st_mode & 0o777 == 0o600
    assert key.parent.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "box" / "processed").stat().st_mode & 0o777 == 0o700
    assert b"PRIVATE KEY" not in (tmp_path / "r" / "dev.pem").read_bytes()
    assert field_inbox.main(args) == 0
    assert key.read_bytes() == original
