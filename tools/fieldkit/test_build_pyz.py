# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the zipapp builder (FIELD-001, VER-001).

Packaging ships ``vakr-fieldkit.pyz`` and releases attach it, so the build
must be byte-reproducible, must embed the version from the single
``VERSION`` file and the public recipient certificate (never a private key),
must leave test modules out, and the result must run ``--help`` on its own.

Dependencies:
    pytest: Test runner.
    build_pyz: Module under test.
    conftest: Throwaway recipient certificate.

Safety:
    Builds into ``tmp_path``; the committed recipient certificate is public.

Example:
    $ uv run pytest tools/fieldkit/test_build_pyz.py
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import build_pyz
from fieldkit_testkit import Pki

REPO = Path(__file__).resolve().parents[2]


def test_zipapp_is_reproducible_and_runs_help(tmp_path: Path) -> None:
    """Two builds are byte-identical, and the archive runs ``--help`` (FIELD-001).

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    first, second = tmp_path / "a" / "kit.pyz", tmp_path / "b" / "kit.pyz"
    assert build_pyz.main(["--output", str(first)]) == 0
    assert build_pyz.main(["--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().startswith(b"#!/usr/bin/env python3\n")
    assert first.stat().st_mode & 0o111
    names = zipfile.ZipFile(first).namelist()
    assert "__main__.py" in names
    assert "fieldkit_embedded.py" in names
    assert not [n for n in names if n.startswith(("test_", "conftest", "testing_", "build_"))]
    for args in (["--help"], ["collect", "--help"]):
        run = subprocess.run(  # noqa: S603
            [sys.executable, str(first), *args], capture_output=True, text=True, check=False
        )
        assert run.returncode == 0, run.stderr
        assert "vakr-fieldkit" in run.stdout
    assert "--community" not in run.stdout.replace("--community-file", "")


def test_zipapp_embeds_version_and_project_values(tmp_path: Path) -> None:
    """``--version`` comes from VERSION; profiles include the application (VER-001).

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    pyz = tmp_path / "kit.pyz"
    assert build_pyz.main(["--output", str(pyz)]) == 0
    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    run = subprocess.run(  # noqa: S603
        [sys.executable, str(pyz), "--version"], capture_output=True, text=True, check=True
    )
    assert run.stdout.strip() == f"vakr-fieldkit {version}"
    embedded = zipfile.ZipFile(pyz).read("fieldkit_embedded.py").decode()
    assert "BEGIN CERTIFICATE" in embedded
    assert "PRIVATE KEY" not in embedded
    assert "'TRANSIT'" in embedded
    assert (
        "host,snmp,paths,vakr"
        in subprocess.run(  # noqa: S603
            [sys.executable, str(pyz), "collect", "--help"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


def test_recipient_override_and_private_key_refusal(tmp_path: Path, pki: Pki) -> None:
    """``--recipient`` replaces the certificate; a key file is refused.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    pyz = tmp_path / "kit.pyz"
    assert build_pyz.main(["--output", str(pyz), "--recipient", str(pki.recipient)]) == 0
    embedded = zipfile.ZipFile(pyz).read("fieldkit_embedded.py").decode()
    marker = pki.recipient.read_text(encoding="ascii").splitlines()[1]
    assert marker in embedded
    bad = ["--output", str(tmp_path / "x.pyz"), "--recipient", str(pki.recipient_key)]
    assert build_pyz.main(bad) == 1
    assert not (tmp_path / "x.pyz").exists()
