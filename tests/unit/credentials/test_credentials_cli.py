"""Tests for ``vakr credentials`` and the ``credentials`` health check.

Covers CRED-001 at the CLI (strict refusal, ``--dev`` warning), the exit-code
contract (0 OK, 1 problems, 2 missing or unreadable), SEC-003 (no command
prints a secret), and the health states OK, WARNING, UNKNOWN, and CRITICAL
(OPS-001, SEC-016).

Dependencies:
    pytest: Fixtures and output capture.

Example:
    $ uv run pytest tests/unit/credentials/test_credentials_cli.py
"""

import argparse
import logging
import os
from pathlib import Path

import pytest

from tests.unit.credentials.credentials_testkit import FakeSecrets, Install, v2c_table
from vakr.cli.main import COMMAND_MODULES, build_parser, main
from vakr.credentials.health import check_credentials
from vakr.health.model import Status
from vakr.health.registry import CHECKS


@pytest.mark.usefixtures("host_policy")
def test_check_valid_file(
    install: Install, fake: FakeSecrets, capsys: pytest.CaptureFixture[str]
) -> None:
    """``check`` exits 0 and prints names, versions, and levels with a v2c posture note.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    assert main(["credentials", "check"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"{install.path}: OK (strict permission checks passed)"
    assert out[1].split() == ["NAME", "KIND", "VERSION", "SECURITY"]
    assert out[2].split() == ["network-snmpv3", "snmp", "3", "authPriv"]
    assert out[3].split() == ["network-v2c", "snmp", "2c", "-"]
    assert out[4].split() == ["metadata-dsn", "secret", "-", "-"]
    assert out[5].startswith("note: network-v2c: SNMPv3 authPriv with SHA-2 and AES")
    assert len(out) == 6


@pytest.mark.usefixtures("host_policy")
def test_check_unsafe_file_strict_and_dev(
    install: Install,
    fake: FakeSecrets,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strict ``check`` exits 1 on unsafe permissions; ``--dev`` warns and exits 0 (CRED-001).

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures output.
        caplog: Captures the development-mode warning.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text(), 0o644)
    assert main(["credentials", "check"]) == 1
    err = capsys.readouterr().err
    assert "file is readable by others (mode 0644)" in err
    assert "expected: mode 0640, owner root:vakr" in err
    with caplog.at_level(logging.WARNING, logger="vakr.credentials.provider"):
        assert main(["credentials", "check", "--dev"]) == 0
    assert "(development mode, 1 permission problem(s) tolerated)" in capsys.readouterr().out
    assert "readable by others (mode 0644); expected 0640 (strict mode refuses this)" in caplog.text


@pytest.mark.usefixtures("host_policy")
def test_check_invalid_file_lists_every_issue(
    install: Install, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validation problems exit 1, one line per issue with file, line, and key.

    Args:
        install: The relocated install.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    install.write('[credentials.a]\nversion = "2c"\n[credentials.b]\nversion = "9"\n')
    assert main(["credentials", "check"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err == [
        f"vakr: {install.path}:1: credentials.a.community: missing required field",
        f'vakr: {install.path}:4: credentials.b.version: must be "2c" or "3"',
    ]


@pytest.mark.usefixtures("host_policy")
def test_missing_and_unreadable_exit_2(
    install: Install, fake: FakeSecrets, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing or unreadable file exits 2 for both ``check`` and ``list``.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    assert main(["credentials", "check"]) == 2
    assert main(["credentials", "list"]) == 2
    assert "no credentials file at" in capsys.readouterr().err
    if os.geteuid() == 0:
        pytest.skip("root reads mode-000 files")
    install.write(fake.file_text(), 0o000)
    assert main(["credentials", "check"]) == 2
    assert "member of the vakr group" in capsys.readouterr().err


@pytest.mark.usefixtures("host_policy")
def test_list_prints_the_table(
    install: Install, fake: FakeSecrets, capsys: pytest.CaptureFixture[str]
) -> None:
    """``list`` shows names, kinds, and versions; an empty file says so.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    install.write(v2c_table("core", fake.community))
    assert main(["credentials", "list"]) == 0
    assert capsys.readouterr().out.splitlines()[1].split() == ["core", "snmp", "2c", "-"]
    install.write("# nothing yet\n")
    assert main(["credentials", "list"]) == 0
    assert capsys.readouterr().out.strip() == "(no credentials defined)"
    install.write("[credentials\n")
    assert main(["credentials", "list"]) == 1


def test_no_command_prints_a_secret() -> None:
    """``credentials`` offers exactly ``check`` and ``list``; there is no ``show`` (SEC-003).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert "vakr.cli.cmd_credentials" in COMMAND_MODULES
    parser = build_parser()
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    sub = top.choices["credentials"]
    actions = next(a for a in sub._actions if isinstance(a, argparse._SubParsersAction))
    assert set(actions.choices) == {"check", "list"}


def test_health_not_installed_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A host without the secrets directory is not an installed node: OK.

    Args:
        tmp_path: Empty install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    result = check_credentials()
    assert result.status is Status.OK
    assert "not an installed vakr node" in result.summary
    assert check_credentials in CHECKS


@pytest.mark.usefixtures("host_policy")
def test_health_states(install: Install, fake: FakeSecrets) -> None:
    """Fresh install WARNING; valid OK with perfdata; unsafe or invalid CRITICAL.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    fresh = check_credentials()
    assert fresh.status is Status.WARNING
    assert "fresh install" in fresh.summary
    assert "docs/operations/credentials.md" in fresh.summary
    install.write(fake.file_text())
    ok = check_credentials()
    assert ok.status is Status.OK
    assert ok.summary == "2 SNMP credential(s), 1 named secret(s); permissions safe"
    assert ok.perfdata[0].render() == "credentials=2.0"
    install.path.chmod(0o660)
    unsafe = check_credentials()
    assert unsafe.status is Status.CRITICAL
    assert "writable by group or others" in unsafe.summary
    install.secrets_dir.chmod(0o777)
    assert "(+1 more)" in check_credentials().summary
    install.secrets_dir.chmod(0o750)
    install.write("[credentials\n")
    invalid = check_credentials()
    assert invalid.status is Status.CRITICAL
    assert "1 problem(s); run `vakr credentials check`" in invalid.summary


@pytest.mark.usefixtures("host_policy")
def test_health_unreadable_is_unknown(install: Install, fake: FakeSecrets) -> None:
    """A file this user cannot read gives UNKNOWN, not a false CRITICAL.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    if os.geteuid() == 0:
        pytest.skip("root reads mode-000 files")
    install.write(fake.file_text(), 0o000)
    result = check_credentials()
    assert result.status is Status.UNKNOWN
    assert "Permission denied" in result.summary
