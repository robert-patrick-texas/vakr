# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests that the SNMP community never leaks, and that walks are bounded (FIELD-002, FIELD-004).

Fake ``snmpbulkwalk`` and ``snmpget`` commands placed first on ``PATH``
record the argument list and environment they were started with, plus the
mode and content of the private ``snmp.conf`` they were pointed at. The
tests then prove the community is in neither argv nor the environment, only
in a 0600 file in a 0700 directory that is gone after the run, and never in
the bundle, even when an agent echoes it back in ``sysContact``.

Dependencies:
    pytest: Test runner and ``monkeypatch``.
    fieldkit: The command-line entry point under test.
    conftest: ``fake_command``.

Safety:
    The community is an obviously fake test string. No network traffic.

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_snmp.py
"""

import json
import os
from pathlib import Path

import fieldkit
import fieldkit_run
import pytest
from fieldkit_snmpconf import CredentialError, parse_devices, validate_community
from fieldkit_testkit import fake_command, read_bundle

TEST_COMMUNITY = "fk-test-community-31"
FAKE_SNMP = r"""
import time
conf_dir = os.environ.get("SNMPCONFPATH", "")
conf = os.path.join(conf_dir, "snmp.conf")
text = open(conf).read() if os.path.exists(conf) else ""
entry = {"argv": sys.argv, "env": dict(os.environ), "conf": text,
         "conf_mode": os.stat(conf).st_mode & 0o777 if text else None,
         "dir_mode": os.stat(conf_dir).st_mode & 0o777 if text else None}
with open(os.environ["FAKE_SNMP_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\n")
host, oid = sys.argv[-2], sys.argv[-1]
if host.startswith("dead"):
    print("Timeout: No Response from " + host, file=sys.stderr)
    sys.exit(1)
community = text.split("defCommunity ", 1)[-1].strip() if text else ""
walks = {
    "1.3.6.1.2.1.1": [
        ".1.3.6.1.2.1.1.1.0 = STRING: Cisco IOS XE Software, Version 17.9.4a",
        ".1.3.6.1.2.1.1.4.0 = STRING: owner " + community,
        ".1.3.6.1.2.1.1.5.0 = STRING: rtr-east-1.corp.example",
        ".1.3.6.1.2.1.1.6.0 = STRING: Dallas DC2"],
    "1.3.6.1.2.1.2.2": [".1.3.6.1.2.1.2.2.1.6.1 = STRING: 0:1a:2b:3c:4d:5e"],
    "1.3.6.1.2.1.31.1.1": [".1.3.6.1.2.1.31.1.1.1.18.1 = STRING: WAN-ISP_Carrier ckt 77123"],
    "1.3.6.1.2.1.4.34": [".1.3.6.1.2.1.4.34.1.3.1.4.10.20.30.5 = INTEGER: 1"],
    "1.3.6.1.2.1.4.20": [".1.3.6.1.2.1.4.20.1.1.10.20.30.5 = IpAddress: 10.20.30.5"],
    "1.3.6.1.2.1.1.3.0": [".1.3.6.1.2.1.1.3.0 = %d" % int(time.time() * 100)],
}
print("\n".join(walks.get(oid, ["." + oid + ".1 = INTEGER: 1"])))
"""


@pytest.fixture
def snmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Install fake SNMP commands and write the community and device files.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Sets ``PATH``, ``HOME``, and the fake's log path.

    Returns:
        dict[str, Path]: ``log``, ``community``, ``devices``, and ``out`` paths.

    Raises:
        None
    """
    bin_dir = tmp_path / "bin"
    for name in ("snmpbulkwalk", "snmpget"):
        fake_command(bin_dir, name, FAKE_SNMP)
    paths = {name: tmp_path / name for name in ("log", "community", "devices", "out")}
    paths["community"].write_text(f"# test only\n{TEST_COMMUNITY}\n", encoding="ascii")
    paths["community"].chmod(0o600)
    paths["devices"].write_text("10.20.30.5\nrtr-east-1.corp.example  # core\n", encoding="ascii")
    paths["out"].mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SNMP_LOG", str(paths["log"]))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return paths


def collect(paths: dict[str, Path], *extra: str) -> tuple[int, list[str]]:
    """Run ``collect --profile snmp`` against the fakes.

    Args:
        paths: Paths from the ``snmp_env`` fixture.
        *extra: Additional options.

    Returns:
        tuple[int, list[str]]: Exit status and the argv that was used.

    Raises:
        SystemExit: On a usage error.
    """
    argv = ["collect", "--profile", "snmp", "--devices", str(paths["devices"]),
            "--community-file", str(paths["community"]), "--no-encrypt",
            "--output-dir", str(paths["out"]), "--uptime-gap", "0", "--device-pause", "0",
            *extra]  # fmt: skip
    return fieldkit.main(argv), argv


def members(out: Path) -> dict[str, bytes]:
    """Read every member of the single bundle in ``out``.

    Args:
        out: Output directory.

    Returns:
        dict[str, bytes]: Member name to content.

    Raises:
        ValueError: If there is not exactly one bundle.
    """
    (bundle,) = out.glob("*.tar.gz")
    return read_bundle(bundle)


def test_community_never_in_argv_environment_or_bundle(snmp_env: dict[str, Path]) -> None:
    """The community reaches net-snmp only through a private 0600 snmp.conf (FIELD-004).

    Args:
        snmp_env: Fake SNMP environment.

    Returns:
        None

    Raises:
        None
    """
    code, argv = collect(snmp_env)
    assert code == 0
    assert all(TEST_COMMUNITY not in a for a in argv)
    calls = [json.loads(line) for line in snmp_env["log"].read_text().splitlines()]
    assert len(calls) == 2 * (5 + 2)  # five walks and two uptime samples per device
    for call in calls:
        assert all(TEST_COMMUNITY not in a for a in call["argv"])
        assert "-c" not in call["argv"]
        assert "-v2c" in call["argv"]
        assert all(TEST_COMMUNITY not in v for v in call["env"].values())
        assert call["conf"] == f"defVersion 2c\ndefCommunity {TEST_COMMUNITY}\n"
        assert (call["conf_mode"], call["dir_mode"]) == (0o600, 0o700)
        assert call["env"]["SNMP_PERSISTENT_DIR"] == call["env"]["SNMPCONFPATH"]
        assert not Path(call["env"]["SNMPCONFPATH"]).exists()
    content = members(snmp_env["out"])
    assert all(TEST_COMMUNITY.encode() not in data for data in content.values())
    manifest = json.loads(content["manifest.json"])
    assert any("redacted" in note for note in manifest["notes"])
    assert manifest["parameters"]["community_source"] == "file"
    assert manifest["errors"] == []
    uptime = json.loads(content["snmp/10.20.30.5/uptime.json"])
    assert uptime["second"]["ticks"] >= uptime["first"]["ticks"]


def test_pseudonymized_bundle_hides_identities_and_is_stable(snmp_env: dict[str, Path]) -> None:
    """Addresses, names, and descriptions are replaced consistently; keywords stay (FIELD-005).

    Args:
        snmp_env: Fake SNMP environment.

    Returns:
        None

    Raises:
        None
    """
    assert collect(snmp_env, "--pseudonymize", "--keep-keyword", "WAN")[0] == 0
    first = members(snmp_env["out"])
    for bundle in snmp_env["out"].glob("*.tar.gz"):
        bundle.unlink()
    assert collect(snmp_env, "--pseudonymize", "--keep-keyword", "WAN")[0] == 0
    second = members(snmp_env["out"])
    walks = {k: v for k, v in first.items() if k.endswith("Table.txt")}
    assert walks == {k: v for k, v in second.items() if k.endswith("Table.txt")}
    blob = b"\n".join([*(name.encode() for name in first), *first.values()])
    for leaked in (b"10.20.30", b"rtr-east-1", b"corp.example", b"Dallas", b"77123", b"0:1a:2b"):
        assert leaked not in blob
    xtable = next(v for k, v in first.items() if k.endswith("ifXTable.txt"))
    assert b"STRING: WAN-" in xtable
    key = Path(os.environ["HOME"]) / ".config" / "fieldkit" / "pseudonym.key"
    assert key.stat().st_mode & 0o777 == 0o600


def test_unprotected_community_file_is_refused(
    snmp_env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A group-readable community file stops the run before anything is collected.

    Args:
        snmp_env: Fake SNMP environment.
        capsys: Captures the error line.

    Returns:
        None

    Raises:
        None
    """
    snmp_env["community"].chmod(0o640)
    assert collect(snmp_env)[0] == 2
    out = capsys.readouterr().out
    assert "readable by group or others" in out
    assert TEST_COMMUNITY not in out
    assert not snmp_env["log"].exists()
    assert list(snmp_env["out"].iterdir()) == []


def test_prompt_needs_a_terminal_and_reads_hidden_input(
    snmp_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``--community-file`` the kit prompts, and refuses if it cannot.

    Args:
        snmp_env: Fake SNMP environment.
        monkeypatch: Simulates a terminal and the hidden prompt.

    Returns:
        None

    Raises:
        None
    """
    base = ["collect", "--profile", "snmp", "--devices", str(snmp_env["devices"]),
            "--no-encrypt", "--output-dir", str(snmp_env["out"]),
            "--uptime-gap", "0", "--device-pause", "0"]  # fmt: skip
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert fieldkit.main(base) == 2
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda *_: TEST_COMMUNITY)
    assert fieldkit.main(base) == 0
    assert all(TEST_COMMUNITY.encode() not in v for v in members(snmp_env["out"]).values())


def test_failed_walks_and_time_budget_are_recorded(
    snmp_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unreachable devices become step errors; an exhausted budget stops the walk loop.

    Args:
        snmp_env: Fake SNMP environment.
        monkeypatch: Forces the time budget to expire.
        capsys: Captures the final line.

    Returns:
        None

    Raises:
        None
    """
    snmp_env["devices"].write_text("dead.example\n10.20.30.5\n", encoding="ascii")
    assert collect(snmp_env, "--extra-oid", ".1.3.6.1.2.1.10.7")[0] == 0
    final = capsys.readouterr().out.strip().splitlines()
    assert len(final) == 1
    assert "step errors" in final[0]
    content = members(snmp_env["out"])
    errors = json.loads(content["manifest.json"])["errors"]
    assert any("snmp.dead.example.system: Timeout" in e for e in errors)
    assert "snmp/10.20.30.5/extra-1.3.6.1.2.1.10.7.txt" in content
    for bundle in snmp_env["out"].glob("*.tar.gz"):
        bundle.unlink()
    monkeypatch.setattr(fieldkit_run.StepLog, "expired", lambda *_: True)
    assert collect(snmp_env)[0] == 0
    notes = json.loads(members(snmp_env["out"])["manifest.json"])["notes"]
    assert "snmp: time budget reached, 2 devices not walked" in notes


@pytest.mark.parametrize(
    "value", ["", " lead", "trail ", '"quoted', "tab\tx", "new\nline", "café", "x" * 256]
)
def test_unsafe_communities_are_refused_without_echo(value: str) -> None:
    """Values net-snmp would alter are refused, and the error never repeats them.

    Args:
        value: Candidate community.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(CredentialError) as excinfo:
        validate_community(value)
    assert not value.strip() or value.strip() not in str(excinfo.value)


@pytest.mark.parametrize("entry", ["-oProxyCommand=x", "rtr 1", "rtr1;reboot", "--help"])
def test_device_entries_cannot_become_options(entry: str) -> None:
    """Entries that could be parsed as options or carry odd characters are refused.

    Args:
        entry: Candidate device line.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="not a host name or address"):
        parse_devices(entry + "\n", "devices.txt", 10)


def test_device_list_dedupes_accepts_agent_specs_and_is_bounded() -> None:
    """Duplicates collapse, net-snmp agent specs are accepted, and the count is capped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    text = "udp6:[2001:db8::1]:161\nrtr1\nrtr1\n# comment\n\n10.0.0.1:1161\n"
    assert parse_devices(text, "d", 10) == ["udp6:[2001:db8::1]:161", "rtr1", "10.0.0.1:1161"]
    with pytest.raises(ValueError, match="--max-devices is 2"):
        parse_devices(text, "d", 2)
