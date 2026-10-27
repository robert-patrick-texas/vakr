# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Parallel-run profiles: MRTG and SmokePing capture, credentials, pseudonyms (FIELD-002..005).

The ``mrtg``, ``smokeping``, and ``acceptance`` profiles turn the parallel
run into evidence for MVP-ACC-03 and MVP-ACC-04. These tests parse a
cfgmaker-style MRTG configuration with every ``Target[]`` form and a Debian
``config.d`` SmokePing layout, run the profiles against recorded MRTG
``.log`` and ``rrdtool info``/``fetch`` output (rrdtool is a stand-in), and
prove the redaction corpus: no community, SNMPv3 user or password, probe
password, RADIUS secret, or URL credential from the fixtures reaches any
bundle member. With ``--pseudonymize`` the hosts in the MRTG table and the
application's device catalog map to the same pseudonyms, so they still
join on the development host.

Dependencies:
    pytest: Test runner.
    fieldkit and its compare modules: The code under test.
    fieldkit_testkit: ``fake_command`` and ``read_bundle``.

Safety:
    Every credential in the fixtures is an obviously fake ``fk-test-...``
    value. DNS lookups are replaced, rrdtool and vakr are stand-ins, and
    nothing leaves the host.

Example:
    $ uv run pytest tools/fieldkit/test_fieldkit_compare.py
"""

import csv
import io
import json
import os
import shutil
import socket
import time
from pathlib import Path
from typing import Any

import fieldkit
import fieldkit_mrtg
import fieldkit_smokeping
import pytest
from fieldkit_acceptance import members
from fieldkit_hosts import host_aliases, resolve_all
from fieldkit_mrtgcfg import parse_files, parse_text
from fieldkit_mrtgmodel import interval_seconds
from fieldkit_mrtgtarget import parse_target
from fieldkit_pseudo import Pseudonymizer
from fieldkit_rrd import compare_window, finest_average
from fieldkit_run import BuildInfo
from fieldkit_scrub import Scrubber
from fieldkit_smokecfg import parse_files as parse_smokeping
from fieldkit_tablescrub import scrub_table
from fieldkit_testkit import fake_command, read_bundle

DATA = Path(__file__).parent / "testdata" / "compare"
NOW = 1792497903  # the fixtures' last MRTG run, 2026-10-20T12:05:03Z
WINDOW = (NOW // 300 * 300 - 7200, NOW // 300 * 300)
CREDENTIALS = [
    "fk-test-mrtg-c1", "fk-test-mrtg-c2", "fk-test@mrtg-c3", "fk-test\\@mrtg-c3", "mrtg-c3",
    "fk-test-mrtg-c4", "fk-test-mrtg-c5", "fk-test-mrtg-commented", "fk-test-script-secret",
    "fk-test-v3-user", "fk-test-v3-auth", "fk-test-v3-priv", "fk-test-contact",
    "fk-test-telnet-login", "fk-test-telnet-password", "fk-test-radius-secret",
    "fk-test-radius-user", "fk-test-radius-password", "fk-test-curl-user", "fk-test-curl-pass",
    "fk-test-alerts", "fk-test-owner", "smokeping_secrets",
]  # fmt: skip
RESOLVED = {"rtr1.example.net": ["192.0.2.10"], "192.0.2.10": [], "192.0.2.20": []}
FAKE_RRDTOOL = r"""
data = os.environ["FK_RRD_DATA"]
with open(os.environ["FK_RRD_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
if len(sys.argv) == 1:
    print("RRDtool 1.7.2  Copyright 1997-2019 by Tobias Oetiker <tobi@oetiker.ch>")
    sys.exit(0)
kind = "smokeping" if "smokeping" in sys.argv[2] else "mrtg-rrd"
sys.stdout.write(open(os.path.join(data, f"{kind}-{sys.argv[1]}.txt")).read())
"""
FAKE_VAKR = r"""
args = sys.argv[1:]
with open(os.environ["FK_VAKR_LOG"], "w") as log:
    targets = args[args.index("--targets") + 1] if "--targets" in args else None
    json.dump({"argv": args, "targets": open(targets).read() if targets else None}, log)
print("#member-stream 1")
print("@member devices.csv table")
print("key,device_id,device,poll_address,aliases,icmp")
print("d0001,0190a3c4-0000-7000-8000-000000000001,rtr1,192.0.2.10,rtr1 192.0.2.10,yes")
print("@member rates/d0001.csv text")
print("key,end,elapsed_s,in_bps,out_bps,if_index,counter_bits,flags")
print("i00001,1792497600.000,60.000,8000,4000,2,64,")
print("@member export.json text")
print(json.dumps({"host": os.uname().nodename, "rows": {"rates": 1}}))
print("@end")
print("vakr acceptance export: 1 interfaces on 1 devices", file=sys.stderr)
"""
APP_INFO = BuildInfo(tool_name="kit", app_name="vakr", acceptance_command=("acceptance", "export"))


def mrtg_main(tmp_path: Path, logformat: str = "rateup") -> Path:
    """Write a main ``mrtg.cfg`` that sets WorkDir and includes the fixture targets.

    Args:
        tmp_path: Per-test directory.
        logformat: ``rateup`` or ``rrdtool``.

    Returns:
        Path: The main configuration file.

    Raises:
        None
    """
    work = tmp_path / "mrtg"
    (work / "rtr1").mkdir(parents=True, exist_ok=True)
    shutil.copy(DATA / "mrtg-192.0.2.10_2.log", work / "192.0.2.10_2.log")
    (work / "rtr1" / "rtr1_gi0_0_1.rrd").write_bytes(b"RRD\0stand-in")
    main = tmp_path / "mrtg.cfg"
    main.write_text(
        f"WorkDir: {work}\nLogFormat: {logformat}\nInclude: {DATA / 'mrtg-targets.cfg'}\n"
    )
    return main


def smokeping_tree(tmp_path: Path) -> Path:
    """Copy the Debian-layout SmokePing fixture and point ``datadir`` into the test.

    Args:
        tmp_path: Per-test directory.

    Returns:
        Path: The copied main ``config``.

    Raises:
        None
    """
    root = tmp_path / "smokeping"
    shutil.copytree(DATA / "smokeping", root)
    datadir = tmp_path / "smokedata"
    (datadir / "Core").mkdir(parents=True)
    names = root / "config.d" / "pathnames"
    names.write_text(names.read_text().replace("@DATADIR@", str(datadir)))
    return root / "config"


@pytest.fixture
def stand_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Put rrdtool and vakr stand-ins first on PATH, fix the window, and stub DNS.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Environment and attribute patches.

    Returns:
        dict[str, Path]: Log files of the stand-ins and the output directory.

    Raises:
        None
    """
    bin_dir = tmp_path / "bin"
    fake_command(bin_dir, "rrdtool", FAKE_RRDTOOL)
    fake_command(bin_dir, "vakr", FAKE_VAKR)
    logs = {"rrd": tmp_path / "rrd.log", "vakr": tmp_path / "vakr.log", "out": tmp_path / "out"}
    logs["out"].mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FK_RRD_DATA", str(DATA))
    monkeypatch.setenv("FK_RRD_LOG", str(logs["rrd"]))
    monkeypatch.setenv("FK_VAKR_LOG", str(logs["vakr"]))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    window = lambda _hours, _step=300: WINDOW  # noqa: E731
    monkeypatch.setattr(fieldkit_mrtg, "compare_window", window)
    monkeypatch.setattr(fieldkit_smokeping, "compare_window", window)
    stub = lambda hosts: {h: RESOLVED.get(h, []) for h in hosts}  # noqa: E731
    monkeypatch.setattr(fieldkit_mrtg, "resolve_all", stub)
    monkeypatch.setattr(fieldkit_smokeping, "resolve_all", stub)
    monkeypatch.setattr(fieldkit, "load_build_info", lambda: APP_INFO)
    monkeypatch.setattr("fieldkit_acceptance.find_app", lambda _name: str(bin_dir / "vakr"))
    return logs


def collect(out: Path, *args: str) -> dict[str, bytes]:
    """Run ``collect`` without encryption or egress and read the bundle.

    Args:
        out: Output directory.
        *args: More options.

    Returns:
        dict[str, bytes]: Members.

    Raises:
        AssertionError: If the run fails.
    """
    argv = ["collect", "--no-encrypt", "--no-egress", "--output-dir", str(out), *args]
    assert fieldkit.main(argv) == 0
    (bundle,) = out.glob("*.tar.gz")
    content = read_bundle(bundle)
    bundle.unlink()
    return content


def table(data: bytes) -> list[dict[str, str]]:
    """Parse a CSV member.

    Args:
        data: Member bytes.

    Returns:
        list[dict[str, str]]: Rows.

    Raises:
        None
    """
    return list(csv.DictReader(io.StringIO(data.decode())))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2:c@192.0.2.10:", ("ifindex", "2", False, 32, "192.0.2.10")),
        ("-10101:c@sw2", ("ifindex", "10101", True, 32, "sw2")),
        ("#Gi0/0/1:c@rtr1:::::2", ("ifname", "Gi0/0/1", False, 64, "rtr1")),
        (r"\Gi0/0/2\ -\ up:c@rtr1:::::2", ("ifdescr", "Gi0/0/2 - up", False, 64, "rtr1")),
        ("/198.51.100.5:c@rtr1:161::::2", ("ip", "198.51.100.5", False, 64, "rtr1")),
        ("!00-1a-2b-3c-4d-60:c@rtr1", ("mac", "00-1a-2b-3c-4d-60", False, 32, "rtr1")),
        ("ifHCOutOctets#Gi1&ifHCInOctets#Gi1:c@r:::::2", ("ifname", "Gi1", True, 64, "r")),
        (
            "1.3.6.1.2.1.31.1.1.1.6.7&1.3.6.1.2.1.31.1.1.1.10.7:c@r",
            ("ifindex", "7", False, 64, "r"),
        ),
        ("4:user@[2001:DB8::7]:::::3", ("ifindex", "4", False, 64, "2001:DB8::7")),
        ("ifInErrors.1&ifOutErrors.1:c@r", ("oid", "", False, 0, "r")),
        ("ifInOctets.1&ifHCOutOctets.1:c@r", ("oid", "", False, 0, "r")),
        ("%6:c@r", ("type", "6", False, 0, "r")),
        ("2:c@a + 3:c@a", ("expression", "", False, 0, "a")),
        ("`/bin/probe --password x`", ("script", "", False, 0, "")),
        ("nonsense", ("invalid", "", False, 0, "")),
    ],
)
def test_target_forms(value: str, expected: tuple[str, str, bool, int, str]) -> None:
    """Every MRTG Target form parses to its kind, key, direction, width, and host.

    Args:
        value: A ``Target[]`` value.
        expected: Kind, key, reversed, counter bits, host.

    Returns:
        None

    Raises:
        None
    """
    spec = parse_target(value)
    assert (spec.kind, spec.key, spec.reversed, spec.counter_bits, spec.host) == expected
    assert "community" not in repr(spec)
    assert "user" not in repr(spec) or spec.kind != "ifindex"


def test_mrtg_configuration_keywords_defaults_and_includes(tmp_path: Path) -> None:
    """Includes, continuation lines, ``[_]`` and ``[^]``, and the kept keywords.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    cfg = parse_files([mrtg_main(tmp_path)])
    by_name = {t.name: t for t in cfg.targets}
    assert len(cfg.files) == 2
    assert cfg.globals["logformat"] == "rateup"
    assert interval_seconds(cfg.globals["interval"]) == 300
    assert "192.0.2.10_3" not in by_name  # commented out by cfgmaker
    first = by_name["192.0.2.10_2"]
    assert first.options() == {"growright", "bits"}
    assert first.values["maxbytes"] == "125000000"
    assert by_name["sw2_reversed"].options() == {"nopercent", "unknaszero"}
    assert by_name["sw2_reversed"].values["directory"] == "sw2"
    rtr = by_name["rtr1_gi0_0_1"].values
    assert (rtr["maxbytes1"], rtr["maxbytes2"], rtr["absmax"]) == ("1250000000", "625000000",
                                                                    "2500000000")  # fmt: skip
    assert by_name["core_v3"].spec.host == "2001:db8::7"
    assert cfg.secrets == {"fk-test-mrtg-c1", "fk-test-mrtg-c2", "fk-test@mrtg-c3",
                           "fk-test-mrtg-c4", "fk-test-mrtg-c5", "fk-test-v3-user"}  # fmt: skip
    assert "fk-test" not in repr(cfg)
    notes = parse_text("Target[x]: 2:c@h\n<html>\n", "bad.cfg").notes
    assert notes == ["bad.cfg:2: not a keyword line; ignored"]


def test_smokeping_debian_layout(tmp_path: Path) -> None:
    """The Targets tree, inherited probes and slaves, subprobe pings, and kept secrets.

    Args:
        tmp_path: Per-test directory.

    Returns:
        None

    Raises:
        None
    """
    cfg = parse_smokeping([smokeping_tree(tmp_path)])
    assert len(cfg.files) == 9
    assert (cfg.step, cfg.pings) == (300, 20)
    assert cfg.datadir == str(tmp_path / "smokedata")
    found = {t.path: (t.host, t.probe, t.slaves, t.master) for t in cfg.targets}
    assert found == {
        "Core/rtr1": ("rtr1.example.net", "FPing", (), True),
        "Core/sw2": ("192.0.2.20", "FPing", ("boomer",), True),
        "Core/edge3": ("192.0.2.30", "FPingCore", (), True),
        "Remote/far1": ("192.0.2.99", "TelnetIOSPing", (), True),
    }
    assert cfg.probe_value("FPingCore", "pings", cfg.pings) == 10
    assert cfg.probe_value("FPing", "pings", cfg.pings) == 20
    assert {"fk-test-telnet-password", "fk-test-radius-secret",
            "https://fk-test-curl-user:fk-test-curl-pass@%host%/health"} <= cfg.secrets  # fmt: skip
    assert "password" not in json.dumps(cfg.probes)
    assert "fk-test" not in repr(cfg)


def test_bundle_never_holds_a_credential(
    tmp_path: Path, stand_ins: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The redaction corpus: no fixture credential reaches any member (FIELD-004).

    Args:
        tmp_path: Per-test directory.
        stand_ins: rrdtool and vakr stand-ins.
        monkeypatch: Not used beyond the fixture.

    Returns:
        None

    Raises:
        None
    """
    del monkeypatch
    for logformat in ("rateup", "rrdtool"):
        content = collect(stand_ins["out"], "--profile", "host,mrtg,smokeping,acceptance",
                          "--mrtg-config", str(mrtg_main(tmp_path, logformat)),
                          "--smokeping-config",
                          str(smokeping_tree(tmp_path / logformat)))  # fmt: skip
        for name, data in content.items():
            for secret in CREDENTIALS:
                assert secret.encode() not in data, f"{secret} in {name} ({logformat})"
    assert "mrtg/rrd/t0002.fetch.txt" in content
    assert "smokeping/rrd/s0001.fetch.txt" in content


def test_mrtg_table_logs_and_rrd_window(tmp_path: Path, stand_ins: dict[str, Path]) -> None:
    """The targets table, the trimmed ``.log``, and an rrd fetch clipped to the finest archive.

    Args:
        tmp_path: Per-test directory.
        stand_ins: rrdtool and vakr stand-ins.

    Returns:
        None

    Raises:
        None
    """
    content = collect(stand_ins["out"], "--profile", "mrtg", "--mrtg-config",
                      str(mrtg_main(tmp_path)))  # fmt: skip
    rows = {r["target"]: r for r in table(content["mrtg/targets.csv"])}
    first = rows["192.0.2.10_2"]
    assert (first["kind"], first["ifindex"], first["counter_bits"]) == ("ifindex", "2", "32")
    assert first["aliases"] == "192.0.2.10"
    assert (first["data"], first["comparable"]) == ("mrtg/logs/t0001.log", "yes")
    assert rows["rtr1_gi0_0_1"]["aliases"] == "rtr1.example.net rtr1 192.0.2.10"
    assert rows["sw2_reversed"]["reversed"] == "yes"
    assert rows["sw2_numeric"]["reversed"] == "yes"
    assert rows["sw2_numeric"]["ifindex"] == "10103"
    assert rows["sw2_gauge"]["note"] == "gauge or absolute values, not interface counters"
    assert rows["wan_sum"]["note"] == "arithmetic over several targets"
    assert rows["by_type"]["comparable"] == "no"
    log = content["mrtg/logs/t0001.log"].decode().splitlines()
    assert log[0] == "1792497903 3627188921 1823477645"
    assert all(int(line.split()[0]) >= WINDOW[0] for line in log[1:])
    assert len(log) == 2 + 24 + 1  # counter, current, two hours of 5-minute rows, the start row
    summary = json.loads(content["mrtg/summary.json"])
    assert (summary["targets"], summary["comparable"]) == (14, 9)
    collect(stand_ins["out"], "--profile", "mrtg", "--compare-hours", "744", "--mrtg-config",
            str(mrtg_main(tmp_path, "rrdtool")))  # fmt: skip
    calls = [json.loads(line) for line in stand_ins["rrd"].read_text().splitlines()]
    fetch = next(c for c in calls if c[0] == "fetch")
    assert fetch[2:5] == ["AVERAGE", "--resolution", "300"]
    start = int(fetch[fetch.index("--start") + 1])
    assert start == max(WINDOW[0], (NOW - 800 * 300) // 300 * 300 + 300)


def test_pseudonymized_tables_still_join(tmp_path: Path, stand_ins: dict[str, Path]) -> None:
    """MRTG aliases and the application's device aliases map alike; no clear name remains.

    Args:
        tmp_path: Per-test directory.
        stand_ins: rrdtool and vakr stand-ins.

    Returns:
        None

    Raises:
        None
    """
    content = collect(stand_ins["out"], "--profile", "mrtg,acceptance", "--pseudonymize",
                      "--mrtg-config", str(mrtg_main(tmp_path)))  # fmt: skip
    mrtg = {r["key"]: r for r in table(content["mrtg/targets.csv"])}
    (device,) = table(content["acceptance/devices.csv"])
    assert set(mrtg["t0002"]["aliases"].split()) & set(device["aliases"].split())
    assert set(mrtg["t0001"]["aliases"].split()) <= set(device["aliases"].split())
    for data in content.values():
        for clear in (b"rtr1.example.net", b"192.0.2.10", b"192.0.2.20", b"198.51.100.5"):
            assert clear not in data
    assert content["acceptance/rates/d0001.csv"].startswith(b"key,end,elapsed_s")
    sent = json.loads(stand_ins["vakr"].read_text())
    assert sent["argv"][:4] == ["acceptance", "export", "--since", "50h"]
    lines = [json.loads(line) for line in sent["targets"].splitlines()]
    assert {"kind": "interface", "device": ["192.0.2.10"], "ifindex": "2"} in lines
    assert {"kind": "interface", "device": ["rtr1.example.net", "rtr1", "192.0.2.10"],
            "ifname": "Gi0/0/1"} in lines  # fmt: skip
    steps = {s["name"]: s for s in json.loads(content["manifest.json"])["steps"]}
    assert steps["acceptance.export"]["returncode"] == 0
    assert steps["acceptance.members"]["members"] == 3


def test_acceptance_profile_skips_and_fails_cleanly(
    tmp_path: Path, stand_ins: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No application is a note; a failing export is a step error; a hand-made targets file passes.

    Args:
        tmp_path: Per-test directory.
        stand_ins: Stand-ins.
        monkeypatch: Replaces the application.

    Returns:
        None

    Raises:
        None
    """
    targets = tmp_path / "targets.txt"
    targets.write_text("icmp 192.0.2.10\n")
    content = collect(stand_ins["out"], "--profile", "acceptance", "--acceptance-targets",
                      str(targets))  # fmt: skip
    assert json.loads(stand_ins["vakr"].read_text())["targets"] == "icmp 192.0.2.10\n"
    assert "acceptance/export.json" in content
    broken = fake_command(tmp_path / "bin2", "vakr", "print('#member-stream 1'); sys.exit(4)")
    monkeypatch.setattr("fieldkit_acceptance.find_app", lambda _name: str(broken))
    manifest = json.loads(collect(stand_ins["out"], "--profile", "acceptance")["manifest.json"])
    steps = {s["name"]: s for s in manifest["steps"]}
    assert steps["acceptance.export"]["error"] == "exit status 4"
    assert "without @end" in steps["acceptance.members"]["error"]
    monkeypatch.setattr("fieldkit_acceptance.find_app", lambda _name: None)
    steps = json.loads(collect(stand_ins["out"], "--profile", "acceptance")["manifest.json"])
    assert any("not installed" in (s.get("note") or "") for s in steps["steps"])


def test_preflight_refuses_missing_configuration(
    tmp_path: Path, stand_ins: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing or unreadable configuration stops the run before anything is collected.

    Args:
        tmp_path: Per-test directory.
        stand_ins: Stand-ins.
        capsys: Captures stdout.

    Returns:
        None

    Raises:
        None
    """
    out = stand_ins["out"]
    base = ["collect", "--no-encrypt", "--output-dir", str(out)]
    assert fieldkit.main([*base, "--profile", "mrtg", "--mrtg-config", "/nonexistent.cfg"]) == 2
    assert "no configuration at /nonexistent.cfg" in capsys.readouterr().out
    assert fieldkit.main([*base, "--profile", "smokeping", "--smokeping-config", "/no/cfg"]) == 2
    assert "--smokeping-config FILE" in capsys.readouterr().out
    assert fieldkit.main([*base, "--profile", "acceptance", "--acceptance-targets", "/no"]) == 2
    assert "is not a file" in capsys.readouterr().out
    locked = tmp_path / "locked.cfg"
    locked.write_text("Target[x]: 2:c@h\n")
    locked.chmod(0)
    if os.geteuid() != 0:
        assert fieldkit.main([*base, "--profile", "mrtg", "--mrtg-config", str(locked)]) == 2
        assert "run with sudo" in capsys.readouterr().out
    assert list(out.iterdir()) == []


def test_member_stream_errors() -> None:
    """A stream without its header, with a bad marker, or cut short is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    good = ["#member-stream 1", "@member a.csv table", "x", "@member b.txt text", "@end"]
    assert [(n, k, b) for n, k, b in members(good)] == [("a.csv", "table", ["x"]),
                                                       ("b.txt", "text", [])]  # fmt: skip
    head = "#member-stream 1"
    for bad, message in ((["nope"], "header"), ([head, "@member a.csv"], "malformed"),
                         ([head, "@member a bin", "@end"], "malformed"),
                         ([head, "@member a.csv text", "x"], "without @end")):  # fmt: skip
        with pytest.raises(ValueError, match=message):
            list(members(bad))


def test_table_scrub_by_column() -> None:
    """Hosts, aliases, addresses, MACs, labels, kept and unknown columns (FIELD-005).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    scrubber = Scrubber(Pseudonymizer(bytes(32), ("UPLINK",)))
    text = ("key,host,aliases,addresses,mac,if_alias,if_index,if_descr\n"
            "k1,rtr1.example.net,rtr1 192.0.2.1,198.51.100.1/30,00:1a:2b:3c:4d:5e,"
            "UPLINK to core9,7,Gi0/0/1 to 192.0.2.9\n")  # fmt: skip
    (row,) = table(scrub_table(scrubber, text).encode())
    p = scrubber.p
    assert row["key"] == "k1"
    assert row["host"] == p.hostname("rtr1.example.net")
    assert row["aliases"] == f"{p.hostname('rtr1')} {p.ip('192.0.2.1')}"
    assert row["addresses"] == p.ip("198.51.100.1") + "/30"
    assert row["mac"] != "00:1a:2b:3c:4d:5e"
    assert row["mac"].count(":") == 5
    assert row["if_alias"].startswith("UPLINK ")
    assert "core9" not in row["if_alias"]
    assert row["if_index"] == "7"
    assert row["if_descr"] == f"Gi0/0/1 to {p.ip('192.0.2.9')}"
    assert scrub_table(scrubber, "") == ""


def test_helpers_window_archive_aliases_and_bounded_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Window alignment, the finest archive, aliases, and DNS bounded per name and overall.

    Args:
        monkeypatch: Replaces ``getaddrinfo``.

    Returns:
        None

    Raises:
        None
    """
    assert compare_window(2, now=NOW) == (NOW // 300 * 300 - 7200, NOW // 300 * 300)
    info = dict(
        line.split(" = ", 1) for line in (DATA / "mrtg-rrd-info.txt").read_text().splitlines()
    )
    assert finest_average(info) == (300, 240000)
    assert finest_average({"step": "60"}) == (60, 0)
    assert host_aliases("rtr1.example.net", ["192.0.2.10", "2001:DB8::1"]) == [
        "rtr1.example.net", "rtr1", "192.0.2.10", "2001:db8::1"]  # fmt: skip

    def lookup(host: str, *_args: Any, **_kwargs: Any) -> list[Any]:
        """Answer, fail, or hang, depending on the name.

        Args:
            host: Name.
            *_args: Ignored.
            **_kwargs: Ignored.

        Returns:
            list[Any]: ``getaddrinfo``-shaped answers.

        Raises:
            OSError: For ``bad.example``.
        """
        if host == "slow.example":
            time.sleep(5)
        if host == "bad.example":
            raise OSError("no such name")
        return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.5", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    started = time.monotonic()
    found = resolve_all(["good.example", "bad.example", "slow.example", "192.0.2.1"],
                        timeout=0.2, budget=1.0)  # fmt: skip
    assert time.monotonic() - started < 2
    assert found == {"good.example": ["192.0.2.5"], "bad.example": [], "slow.example": [],
                     "192.0.2.1": []}  # fmt: skip
