# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S101 - pytest assertions (the repo's tests/** ignore does not reach here)
"""Static checks of the package spec, maintainer scripts, and build inputs (PKG-001..PKG-005).

The container tests (``make package-test``) prove the built package works;
these fast checks pin the decisions behind it so a careless edit fails in
seconds: the files nfpm may place outside ``/opt/vakr``, the absence of
conffiles (CFGM-005), strict maintainer scripts that never overwrite
operator config or delete telemetry history, lintian overrides that are
justified and confined to the bundled runtime, the release signer identity
(ADR-026), and pinned build inputs.

Dependencies:
    pytest: Test runner.
    subprocess: ``sh -n`` syntax checks of the maintainer scripts.

Safety:
    Read-only; nothing is built or installed.

Example:
    $ uv run pytest packaging/tests
"""

import re
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parent
OVERRIDABLE = {
    # Owner decision (ADR-025) and bundled-interpreter tags (PKG-004).
    "dir-or-file-in-opt",
    "embedded-library",
    "hardening-no-pie",
    "package-installs-python-pycache-dir",
    "python3-script-but-no-python3-dep",
    "unstripped-binary-or-object",
    "unusual-interpreter",
    "wrong-path-for-interpreter",
}

UNIT_OVERRIDES = {
    # Owner approval 2026-10-20: vakr's services join the package's own
    # vakr.target (6.2), which lintian calls unusual. Units must live in
    # /usr/lib/systemd/system, so this is the one override outside /opt/vakr.
    "systemd-service-file-refers-to-unusual-wantedby-target": "[usr/lib/systemd/system/vakr-",
}


def nfpm_entries() -> list[dict[str, str]]:
    """Extract the ``contents`` entries of ``nfpm.yaml`` without a YAML library.

    Args:
        None

    Returns:
        list[dict[str, str]]: ``src``, ``dst``, and optional ``type`` per entry.

    Raises:
        OSError: If the spec cannot be read.
    """
    entries: list[dict[str, str]] = []
    for line in (PKG / "nfpm.yaml").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*(-\s+)?(src|dst|type|packager):\s*(\S+)", line)
        if match and match.group(2) == "src" and match.group(1):
            entries.append({})
        if match and entries:
            entries[-1][match.group(2)] = match.group(3)
    return entries


def test_nfpm_places_only_integration_points_outside_opt() -> None:
    """PKG-002, CFGM-005: everything under /opt/vakr; no conffiles at all.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If an unexpected destination or a config type appears.
    """
    entries = nfpm_entries()
    outside = sorted(e["dst"] for e in entries if not e["dst"].startswith("/opt/vakr"))
    assert outside == [
        "/usr/bin/vakr",
        "/usr/lib/systemd/system/vakr-api.service",
        "/usr/lib/systemd/system/vakr-collector.service",
        "/usr/lib/systemd/system/vakr-update.service",
        "/usr/lib/systemd/system/vakr-update.timer",
        "/usr/lib/systemd/system/vakr.target",
        "/usr/lib/sysusers.d/vakr.conf",
        "/usr/lib/tmpfiles.d/vakr.conf",
        "/usr/share/doc/vakr/changelog.gz",
        "/usr/share/doc/vakr/copyright",
        "/usr/share/lintian/overrides/vakr",
    ]
    assert not [e for e in entries if e.get("type", "").startswith("config")]
    for entry in entries:
        if entry["src"].startswith("packaging/"):
            assert (REPO / entry["src"]).is_file(), entry["src"]


def test_nfpm_version_and_dependencies() -> None:
    """PKG-001, VER-001: version from the build, verbatim; required dependencies.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the version source or a dependency changes.
    """
    spec = (PKG / "nfpm.yaml").read_text(encoding="utf-8")
    assert "version: ${VAKR_DEB_VERSION}\n" in spec
    assert "version_schema: none\n" in spec
    for dep in ("ca-certificates", "openssh-client", "openssl", "systemd", "libc6 (>= "):
        assert f"      - {dep}" in spec
    assert "      - snmp\n" in spec


@pytest.mark.parametrize("script", ["postinst", "prerm", "postrm"])
def test_maintainer_scripts_are_strict_posix_sh(script: str) -> None:
    """Debian policy: maintainer scripts use ``set -e`` and parse with ``sh -n``.

    Args:
        script: Maintainer script name.

    Returns:
        None

    Raises:
        AssertionError: If the script is not strict or does not parse.
    """
    path = PKG / "scripts/deb" / script
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "\nset -e\n" in text
    assert path.stat().st_mode & 0o111, "maintainer script must be executable"
    subprocess.run(["sh", "-n", str(path)], check=True)  # noqa: S603, S607


def test_postinst_never_overwrites_operator_config() -> None:
    """CFGM-005: config.toml is created only when absent, and upgrades try-restart.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the guard or restart behaviour is missing.
    """
    postinst = (PKG / "scripts/deb/postinst").read_text(encoding="utf-8")
    assert '[ -e "$CONFIG" ] && return 0' in postinst
    assert "systemctl try-restart vakr.target" in postinst
    assert "systemd-sysusers vakr.conf" in postinst
    assert "systemd-tmpfiles --create vakr.conf" in postinst


def test_postrm_purge_keeps_telemetry_history() -> None:
    """PKG-005: purge removes /etc/vakr but never /var/lib/vakr.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If purge would delete history or skip the notice.
    """
    postrm = (PKG / "scripts/deb/postrm").read_text(encoding="utf-8")
    assert "rm -rf /etc/vakr /var/cache/vakr" in postrm
    assert "rm -rf /var/lib/vakr" not in postrm
    assert "kept telemetry history in /var/lib/vakr" in postrm


def test_lintian_overrides_are_justified_and_confined() -> None:
    """PKG-004: only allowed tags, each with a written reason, all under /opt/vakr.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If an override is unjustified or too broad.
    """
    lines = (PKG / "lintian/vakr").read_text(encoding="utf-8").splitlines()
    overrides = [(n, line) for n, line in enumerate(lines) if line.startswith("vakr: ")]
    assert overrides
    for n, line in overrides:
        tag = line.split()[1]
        assert tag in OVERRIDABLE or tag in UNIT_OVERRIDES, (
            f"override for {tag} needs owner approval"
        )
        assert line.rstrip().endswith("]"), f"override without a pointer: {line}"
        confined = UNIT_OVERRIDES.get(tag, "[opt/vakr/")
        assert confined in line, f"override outside {confined.strip('[')}: {line}"
        preceding = lines[n - 1]
        assert preceding.startswith("#") or preceding.startswith("vakr: "), "no justification"


def test_release_signers_entries_use_the_release_identity() -> None:
    """ADR-026: any listed key uses principal and namespace ``vakr-release``.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If a key line uses another identity or namespace.
    """
    for line in (PKG / "release-signers").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.startswith('vakr-release namespaces="vakr-release" ssh-'), line


def test_build_inputs_are_pinned() -> None:
    """PKG-004: uv, nfpm, Go, and the build backend are pinned and verified.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If an unpinned input appears.
    """
    dockerfile = (PKG / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"FROM ghcr.io/astral-sh/uv:0\.13\.3@sha256:[0-9a-f]{64} ", dockerfile)
    assert re.search(r"FROM golang:1\.27\.3@sha256:[0-9a-f]{64} ", dockerfile)
    assert "nfpm/v2/cmd/nfpm@v2.48.0" in dockerfile
    requirements = [
        line
        for line in (PKG / "build-constraints.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith(("#", " "))
    ]
    assert requirements
    assert all(re.fullmatch(r"[a-z0-9-]+==\S+ \\", line) for line in requirements)
