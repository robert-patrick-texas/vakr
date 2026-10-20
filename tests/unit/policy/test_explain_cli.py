"""CFG-009, POLICY-004/006: ``vakr policy explain`` and ``vakr policy validate``.

The fixtures in ``data/`` are an IOS XE edge router and access switch and
the example policies from ``docs/operations/policies.md``.

Dependencies:
    pytest: Output capture, temporary directories, environment patching.
    vakr.cli.main: The CLI entry point under test.

Example:
    $ uv run pytest tests/unit/policy/test_explain_cli.py
"""

import json
import shutil
from pathlib import Path

import pytest

from vakr.cli.main import main

DATA = Path(__file__).parent / "data"
FACTS = DATA / "edge-facts.toml"
POLICIES = DATA / "policies.toml"
RULE_NAMES = ["no-logical-noise", "access-user-ports", "wan-fast-poll", "edge-mgmt-svi"]


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run ``vakr`` and capture its result.

    Args:
        capsys: Pytest output capture.
        *argv: Arguments after ``vakr``.

    Returns:
        tuple[int, str, str]: Exit status, standard output, standard error.

    Raises:
        None
    """
    status = main(list(argv))
    out, err = capsys.readouterr()
    return status, out, err


def test_cfg_009_explain_names_every_rule_deterministically(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """CFG-009: every rule appears for every interface, and two runs print the same bytes.

    Args:
        capsys: Pytest output capture.
        tmp_path: Empty configuration directory, so only packaged keywords apply.

    Returns:
        None

    Raises:
        None
    """
    argv = ["policy", "explain", "--facts", str(FACTS), "--policies", str(POLICIES)]
    argv += ["--config-dir", str(tmp_path)]
    status, first, err = _run(capsys, *argv)
    assert (status, err) == (0, "")
    assert _run(capsys, *argv)[1] == first
    blocks = first.split("\n\n")
    assert len(blocks) == 8
    for block in blocks:
        positions = [block.index(f" {name} [") for name in RULE_NAMES]
        assert positions == sorted(positions), block
    suppressed = "suppressed (not monitored; still inventoried) [rule no-logical-noise]"
    assert f"interface edge-rtr1 Loopback0 (ifIndex 21)\n  decision: {suppressed}" in first
    assert (
        "interface edge-rtr1 Vlan100 (ifIndex 20)\n  decision: monitored [rule edge-mgmt-svi]"
        in first
    )
    assert '      decided by scope groups: "wan-routers" matches none of access-switches' in first
    assert "  polling_interval = 30s from rule wan-fast-poll" in first
    assert '      why: description keyword ISP matched "ISP"; outranks rules' in first


def test_explain_json_reports_decisions_and_traces(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """POLICY-004: JSON output carries the decision, effective rule, and every rule's trace.

    Args:
        capsys: Pytest output capture.
        tmp_path: Empty configuration directory.

    Returns:
        None

    Raises:
        None
    """
    argv = ["policy", "explain", "--facts", str(FACTS), "--policies", str(POLICIES), "--json"]
    status, out, _err = _run(capsys, *argv, "--config-dir", str(tmp_path))
    assert status == 0
    found = {
        (e["interface"]["device"], e["interface"]["ifname"]): e
        for e in json.loads(out)["explanations"]
    }
    isp = found[("edge-rtr1", "GigabitEthernet0/0/0")]
    assert isp["decision"] == {
        "monitored": True,
        "inventoried": True,
        "effective_rule": None,
        "interest": "primary",
        "sticky": True,
        "wan_circuit": True,
        "settings": {"polling_interval": 30.0},
        "description_tags": ["isp"],
        "operator_tags": [],
        "not_yet_classified": False,
    }
    assert [r["name"] for r in isp["rules"]] == RULE_NAMES
    assert isp["rules"][0]["decided_by"]["field"] == "ifname"
    user = found[("acc-sw7", "GigabitEthernet1/0/7")]["decision"]
    assert [user[k] for k in ("monitored", "effective_rule", "not_yet_classified")] == [
        False, "access-user-ports", True,
    ]  # fmt: skip
    printers = found[("acc-sw7", "GigabitEthernet1/0/8")]["decision"]
    assert (printers["operator_tags"], printers["not_yet_classified"]) == (["printers"], False)
    tunnel = next(v for v in found[("edge-rtr1", "Tunnel10")]["values"] if v["name"] == "monitor")
    assert [tunnel[k] for k in ("value", "source", "origin")] == [
        "exclude",
        "rule",
        "no-logical-noise",
    ]


def test_json_facts_and_installed_policies_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Facts may be JSON; without ``--policies`` the configuration directory's file is used.

    Args:
        capsys: Pytest output capture.
        tmp_path: Relocated installation root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        None

    Raises:
        None
    """
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"interfaces": [{"device": {"name": "edge-rtr1"},
                     "interface": {"ifname": "Vlan100", "ifalias": ""}}]}))  # fmt: skip
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    status, out, _err = _run(capsys, "policy", "explain", "--facts", str(facts))
    assert status == 0
    assert "  (no rules)" in out
    assert "interface edge-rtr1 Vlan100\n" in out
    config_dir = tmp_path / "etc/vakr"
    config_dir.mkdir(parents=True)
    shutil.copy(POLICIES, config_dir / "policies.toml")
    status, out, _err = _run(capsys, "policy", "explain", "--facts", str(facts))
    assert status == 0
    assert "decision: monitored [rule edge-mgmt-svi]" in out


def test_validate_reports_success_and_every_problem(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """``vakr policy validate`` exits 0 for valid files and 1 with every problem listed.

    Args:
        capsys: Pytest output capture.
        tmp_path: Configuration directory and scratch files.

    Returns:
        None

    Raises:
        None
    """
    status, out, _err = _run(
        capsys, "policy", "validate", str(POLICIES), "--config-dir", str(tmp_path)
    )
    assert (status, out) == (
        0,
        f"policies valid: 4 rule(s) in {POLICIES}; 3 description keyword(s)\n",
    )
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[[interface_policy.rules]]\nname = "r"\naction = "drop"\n'
        'match.ifalias = ["re:(\\\\w+\\\\s?)*"]\nsettings.polling_interval = "5s"\n'
    )
    status, _out, err = _run(capsys, "policy", "validate", str(bad), "--config-dir", str(tmp_path))
    assert status == 1
    assert (
        f"vakr policy: {bad}: interface_policy.rules[0].match.ifalias[0]: nested quantifier" in err
    )
    assert "interface_policy.rules[0].action: expected one of: include, exclude" in err
    assert "settings.polling_interval: must be between 30s and 1h" in err
    assert err.endswith("vakr policy: 3 problem(s) found\n")


def test_unsafe_keyword_in_configuration_fails_validation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """CFG-005: an unsafe ``re:`` keyword is caught with its configuration path.

    Args:
        capsys: Pytest output capture.
        tmp_path: Configuration directory.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "config.toml").write_text(
        'config_version = 1\n[[interface_classification.keywords]]\nkeyword = "re:(a|aa)+"\n'
        'tag = "x"\n'
    )
    argv = ["--config-dir", str(tmp_path)]
    status, _out, err = _run(capsys, "policy", "validate", str(POLICIES), *argv)
    assert status == 1
    assert "interface_classification.keywords[3].keyword: alternatives that can start" in err
    status, _out, err = _run(capsys, "policy", "explain", "--facts", str(FACTS), *argv)
    assert status == 1
    assert "keywords[3].keyword" in err


def test_invalid_configuration_and_files_exit_1(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Broken configuration, unreadable files, and malformed documents exit 1 with a reason.

    Args:
        capsys: Pytest output capture.
        tmp_path: Scratch directory.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "config.toml").write_text("config_version = 1\n[polling]\nfast = true\n")
    status, _out, err = _run(
        capsys, "policy", "validate", str(POLICIES), "--config-dir", str(tmp_path)
    )
    assert status == 1
    assert "polling" in err
    cases = {
        tmp_path / "missing.toml": "cannot read",
        tmp_path / "broken.toml": "not valid TOML",
        tmp_path / "list.json": "JSON document must be a table",
        tmp_path / "huge.toml": "larger than 4194304 bytes",
    }
    (tmp_path / "broken.toml").write_text("[[interfaces]\n")
    (tmp_path / "list.json").write_text("[]")
    (tmp_path / "huge.toml").write_text("#" * (4 * 1024 * 1024 + 1))
    for path, reason in cases.items():
        status, _out, err = _run(
            capsys, "policy", "explain", "--facts", str(path), "--config-dir", str(tmp_path)
        )
        assert status == 1
        assert f"{path}: {reason}" in err


def test_bad_facts_are_reported_with_paths(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Facts files are validated like configuration: unknown keys and bad values name a path.

    Args:
        capsys: Pytest output capture.
        tmp_path: Scratch directory.

    Returns:
        None

    Raises:
        None
    """
    facts = tmp_path / "facts.toml"
    facts.write_text(
        "[[interfaces]]\ndevice = { site = 'nyc01' }\n"
        "interface = { ifname = 'Gi0/0/0', oper_status = 'sideways',"
        " addresses = ['300.1.1.1/24'], ifindex = 0, mtu = 1500 }\n"
        "annotations = { interest = 'primary', high_priority = false }\n"
    )
    status, _out, err = _run(
        capsys, "policy", "explain", "--facts", str(facts), "--config-dir", str(tmp_path)
    )
    assert status == 1
    for problem in (
        "interfaces[0].device.name: required",
        "interfaces[0].interface.mtu: unknown key",
        "interfaces[0].interface.oper_status: expected one of: up, down",
        "interfaces[0].interface.addresses[0]: expected an IPv4 or IPv6 address/prefix",
        "interfaces[0].interface.ifindex: must be at least 1",
        "interfaces[0].annotations.high_priority: contradicts",
    ):
        assert problem in err
    facts.write_text("interfaces = []\n")
    status, _out, err = _run(capsys, "policy", "explain", "--facts", str(facts))
    assert status == 1
    assert "expected a list of 1 to 10000" in err


def test_status_values_accept_names_and_numbers(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """IF-MIB states may be written as names in either style or as the agent's integers.

    Args:
        capsys: Pytest output capture.
        tmp_path: Scratch directory.

    Returns:
        None

    Raises:
        None
    """
    facts = tmp_path / "facts.json"
    interface = {"ifname": "Tunnel10", "oper_status": 7, "admin_status": "UP"}
    interface["addresses"] = ["2001:DB8::1/64"]
    entry = {"device": {"name": "edge-rtr1"}, "interface": interface}
    facts.write_text(json.dumps({"interfaces": [entry]}))
    policy = tmp_path / "p.toml"
    policy.write_text(
        '[[interface_policy.rules]]\nname = "r"\naction = "exclude"\n'
        'match.oper_status = ["lower_layer_down"]\nmatch.addresses = ["2001:db8::*"]\n'
    )
    argv = ["policy", "explain", "--facts", str(facts), "--policies", str(policy), "--json"]
    status, out, _err = _run(capsys, *argv, "--config-dir", str(tmp_path))
    assert status == 0
    (only,) = json.loads(out)["explanations"]
    assert only["decision"]["monitored"] is False
    conditions = [c["value"] for c in only["rules"][0]["conditions"]]
    assert conditions == ["2001:db8::1/64", "lower_layer_down"]  # MatchField order


def test_policy_without_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """``vakr policy`` alone prints help and exits 2.

    Args:
        capsys: Pytest output capture.

    Returns:
        None

    Raises:
        None
    """
    status, _out, err = _run(capsys, "policy")
    assert status == 2
    assert "explain" in err
