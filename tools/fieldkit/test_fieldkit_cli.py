# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests of ``collect``: encryption by default, final line, profiles (FIELD-002..009).

These run the real command-line entry point against stand-in commands
(``ping``, ``vakr``, ``journalctl``) and a throwaway recipient certificate,
and check what an operator sees: exactly one stdout line naming the output
file and the next step, an encrypted ``*.tar.gz.p7m`` with the plaintext
removed, and clear refusals before any collection when inputs are wrong.

Dependencies:
    pytest: Test runner.
    fieldkit, fieldkit_crypto: Entry point and decryption for checking.
    conftest: Throwaway PKI and ``fake_command``.

Safety:
    Output goes to ``tmp_path``; the egress test is disabled or pointed at
    an unroutable loopback port, so no traffic leaves the host.

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_cli.py
"""

import json
import os
from pathlib import Path

import fieldkit
import fieldkit_collect
import pytest
from fieldkit_crypto import decrypt
from fieldkit_run import BuildInfo
from fieldkit_testkit import Pki, fake_command, read_bundle

FAKE_PING = r"""
print("PING %s 56(84) bytes of data." % sys.argv[-1])
print("--- %s ping statistics ---" % sys.argv[-1])
print("20 packets transmitted, 19 received, 5% packet loss, time 3805ms")
print("rtt min/avg/max/mdev = 55.104/57.210/61.003/1.402 ms")
"""
FAKE_VAKR = r"""
if sys.argv[1:] == ["--version"]:
    print("vakr 0.0.0-test")
elif sys.argv[1] == "doctor":
    print("[OK      ] version: vakr 0.0.0-test")
    sys.exit(1)
else:
    print("vakr: %s is not implemented in this release yet" % sys.argv[1], file=sys.stderr)
    sys.exit(3)
"""
APP_INFO = BuildInfo(
    tool_name="kit",
    app_name="vakr",
    app_commands=(("--version",), ("doctor",), ("config", "show-effective")),
    app_units=("vakr*",),
)


def test_default_run_encrypts_removes_plaintext_and_prints_one_line(
    tmp_path: Path, pki: Pki, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bundle is encrypted by default and stdout is exactly one line (FIELD-006, FIELD-009).

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.
        capsys: Captures stdout.

    Returns:
        None

    Raises:
        None
    """
    argv = ["collect", "--recipient", str(pki.recipient), "--output-dir", str(tmp_path),
            "--egress-url", "https://127.0.0.1:9/x", "--egress-head", "https://127.0.0.1:9/y",
            "--egress-timeout", "1"]  # fmt: skip
    assert fieldkit.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    (sealed,) = tmp_path.glob("field-*.tar.gz.p7m")
    assert str(sealed) in lines[0]
    assert "next: scp it to" in lines[0]
    assert "step errors" in lines[0]  # the unroutable egress targets fail, as they should
    assert not list(tmp_path.glob("*.tar.gz"))
    plain = tmp_path / "plain.tar.gz"
    pki.recipient_key.chmod(0o600)
    decrypt("openssl", pki.recipient, pki.recipient_key, sealed, plain)
    content = read_bundle(plain)
    manifest = json.loads(content["manifest.json"])
    assert manifest["encryption"]["enabled"] is True
    assert "test recipient" in manifest["encryption"]["recipient"]["subject"]
    egress = json.loads(content["host/egress.json"])
    assert [e["method"] for e in egress] == ["GET", "HEAD"]
    assert all(e["error"].startswith("connect:") for e in egress)


def test_keep_plaintext_and_no_encrypt(tmp_path: Path, pki: Pki) -> None:
    """``--keep-plaintext`` keeps both files; ``--no-encrypt`` writes only the tarball.

    Args:
        tmp_path: Per-test directory.
        pki: Session PKI.

    Returns:
        None

    Raises:
        None
    """
    keep, plain = tmp_path / "keep", tmp_path / "plain"
    keep.mkdir()
    plain.mkdir()
    common = ["collect", "--no-egress", "--recipient", str(pki.recipient)]
    assert fieldkit.main([*common, "--output-dir", str(keep), "--keep-plaintext"]) == 0
    assert len(list(keep.glob("*.tar.gz"))) == 1
    assert len(list(keep.glob("*.tar.gz.p7m"))) == 1
    assert fieldkit.main([*common, "--output-dir", str(plain), "--no-encrypt"]) == 0
    assert [p.suffix for p in plain.iterdir()] == [".gz"]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--profile", "bogus"], "unknown profile bogus"),
        (["--profile", "snmp"], "--profile snmp needs --devices"),
        (["--profile", "paths"], "--profile paths needs --peer"),
        (["--peer=-oops"], "not a host name"),
        (["--extra-oid", "IF-MIB::ifTable"], "--extra-oid must be numeric"),
        (["--output-dir", "/nonexistent/dir"], "is not a directory"),
    ],
)
def test_preflight_refuses_bad_inputs_before_collecting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    """Every input problem is reported up front, with exit 2 and no bundle.

    Args:
        tmp_path: Per-test directory.
        capsys: Captures stdout.
        extra: Options that make the run invalid.
        message: Expected explanation.

    Returns:
        None

    Raises:
        None
    """
    argv = ["collect", "--no-encrypt", "--output-dir", str(tmp_path), *extra]
    assert fieldkit.main(argv) == 2
    out = capsys.readouterr().out
    assert message in out
    assert "no bundle was written" in out
    assert list(tmp_path.iterdir()) == []


def test_encryption_needs_a_recipient_and_modern_openssl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a built-in certificate, or with old OpenSSL, the run refuses to start.

    Args:
        tmp_path: Per-test directory.
        capsys: Captures stdout.

    Returns:
        None

    Raises:
        None
    """
    assert fieldkit.main(["collect", "--output-dir", str(tmp_path)]) == 2
    assert "no built-in recipient certificate" in capsys.readouterr().out
    old = fake_command(tmp_path / "bin", "openssl", "print('OpenSSL 1.1.1w  11 Sep 2023')")
    assert fieldkit.main(["collect", "--openssl", str(old), "--output-dir", str(tmp_path)]) == 2
    assert "OpenSSL 3.0 or newer is required" in capsys.readouterr().out


def test_paths_and_app_profiles_with_stand_in_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``paths`` parses ping statistics; the app profile skips unbuilt commands (FIELD-002).

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Puts stand-ins first on ``PATH`` and sets build info.

    Returns:
        None

    Raises:
        None
    """
    bin_dir = tmp_path / "bin"
    fake_command(bin_dir, "ping", FAKE_PING)
    fake_command(bin_dir, "vakr", FAKE_VAKR)
    fake_command(bin_dir, "journalctl", "print('2026-10-20T10:00:00+0000 host vakr[1]: started')")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(fieldkit, "load_build_info", lambda: APP_INFO)
    out = tmp_path / "out"
    out.mkdir()
    argv = ["collect", "--profile", "paths,vakr", "--peer", "vakr-west.example", "--no-encrypt",
            "--output-dir", str(out)]  # fmt: skip
    assert fieldkit.main(argv) == 0
    (bundle,) = out.glob("*.tar.gz")
    content = read_bundle(bundle)
    (ping,) = json.loads(content["paths/ping.json"])
    assert ping == {"kind": "peer", "target": "vakr-west.example", "transmitted": 20,
                    "received": 19, "loss_pct": 5.0, "min_ms": 55.104, "avg_ms": 57.21,
                    "max_ms": 61.003, "mdev_ms": 1.402}  # fmt: skip
    assert content["vakr/version.txt"] == b"vakr 0.0.0-test\n"
    assert b"[OK      ]" in content["vakr/doctor.txt"]
    assert "vakr/config-show-effective.txt" not in content
    assert b"started" in content["vakr/journal.txt"]
    steps = {s["name"]: s for s in json.loads(content["manifest.json"])["steps"]}
    assert "not implemented" in steps["vakr.config-show-effective"]["note"]
    assert steps["vakr.doctor"]["returncode"] == 1


def test_app_profile_skips_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the application on ``PATH`` the profile records a note and moves on.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Empties ``PATH`` of the application and sets build info.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(fieldkit, "load_build_info", lambda: APP_INFO)
    monkeypatch.setattr("fieldkit_app.find_app", lambda *_: None)
    assert (
        fieldkit.main(
            ["collect", "--profile", "vakr", "--no-encrypt", "--output-dir", str(tmp_path)]
        )
        == 0
    )
    (bundle,) = tmp_path.glob("*.tar.gz")
    steps = json.loads(read_bundle(bundle)["manifest.json"])["steps"]
    assert any(s["name"] == "vakr.detect" and "not installed" in s["note"] for s in steps)


def test_no_command_and_final_line_wording(capsys: pytest.CaptureFixture[str]) -> None:
    """No subcommand is a usage error; the unencrypted final line warns.

    Args:
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    assert fieldkit.main([]) == 2
    assert "collect" in capsys.readouterr().err
    line = fieldkit_collect.final_line(BuildInfo(), Path("b.tar.gz"), 0, encrypted=False)
    assert "NOT encrypted" in line
    assert "\n" not in line
