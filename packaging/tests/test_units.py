# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S101 - pytest assertions (the repo's tests/** ignore does not reach here)
"""Static checks of the systemd units, sysusers.d, and tmpfiles.d entries (UPD-003, PKG-003).

The container tests (``make package-test``) prove the installed units work;
these fast checks pin the decisions behind them so a careless edit fails in
seconds: the timer schedule from Appendix E.14, the update unit's command
and its isolation from ``vakr.target``, the service account, and the
directory owners and modes from section 11.1.

Dependencies:
    pytest: Test runner.

Safety:
    Read-only; nothing is built or installed.

Example:
    $ uv run pytest packaging/tests
"""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]


def unit(name: str) -> dict[str, list[tuple[str, str]]]:
    """Parse a systemd unit file into sections of ordered key/value pairs.

    Args:
        name: File name under ``packaging/systemd``.

    Returns:
        dict[str, list[tuple[str, str]]]: Section name to its settings;
        repeated keys are kept, as systemd allows.

    Raises:
        OSError: If the file cannot be read.
    """
    sections: dict[str, list[tuple[str, str]]] = {}
    current = ""
    for raw in (PKG / "systemd" / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = line.strip("[]")
            sections[current] = []
        else:
            key, _, value = line.partition("=")
            sections[current].append((key, value))
    return sections


def table(path: Path) -> list[list[str]]:
    """Split a sysusers.d or tmpfiles.d file into fields, honouring quotes.

    Args:
        path: Configuration file.

    Returns:
        list[list[str]]: One field list per non-comment line.

    Raises:
        OSError: If the file cannot be read.
    """
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            rows.append([f.strip('"') for f in re.findall(r'"[^"]*"|\S+', line)])
    return rows


def test_update_timer_is_daily_randomised_and_persistent() -> None:
    """UPD-003, Appendix E.14: daily, RandomizedDelaySec=4h, Persistent=true.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the schedule drifts from the blueprint defaults.
    """
    timer = unit("vakr-update.timer")
    assert dict(timer["Timer"]) == {
        "OnCalendar": "daily",
        "RandomizedDelaySec": "4h",
        "Persistent": "true",
        "Unit": "vakr-update.service",
    }
    assert timer["Install"] == [("WantedBy", "timers.target")]


def test_update_service_runs_unattended_self_update_outside_the_target() -> None:
    """UPD-003: oneshot self-update, never PartOf vakr.target, timer-activated only.

    The update restarts vakr.target, so membership would make it kill itself.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the command or wiring changes.
    """
    service = unit("vakr-update.service")
    settings = dict(service["Service"])
    assert settings["Type"] == "oneshot"
    assert settings["ExecStart"] == (
        "/opt/vakr/venv/bin/vakr self-update --yes --if-newer --unattended"
    )
    assert "PartOf" not in dict(service["Unit"])
    assert "Install" not in service


def test_collector_unit_joins_the_target_hardened_and_disabled() -> None:
    """6.2, 15.3, SVC-009, PKG-003: vakr user, no capability, reload by SIGHUP, no 78 restarts.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the collector unit's contract changes.
    """
    collector = unit("vakr-collector.service")
    settings = dict(collector["Service"])
    assert settings["ExecStart"] == "/opt/vakr/venv/bin/vakr collector run"
    assert (settings["Type"], settings["User"], settings["Group"]) == ("notify", "vakr", "vakr")
    assert settings["ExecReload"] == "/bin/kill -HUP $MAINPID"
    assert settings["RestartPreventExitStatus"] == "78"
    assert settings["TimeoutStopSec"] == "120s"  # > shutdown_grace + stop budget (E.27)
    for key, value in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"),
                       ("ReadWritePaths", "/var/lib/vakr /run/vakr"),  # E.33 snapshot
                       ("ProtectHome", "yes"), ("PrivateTmp", "yes"),
                       ("CapabilityBoundingSet", "")):  # fmt: skip
        assert settings[key] == value, key
    assert ("PartOf", "vakr.target") in collector["Unit"]
    assert collector["Install"] == [("WantedBy", "vakr.target")]
    drop_in = PKG / "examples/vakr-collector.service.d"
    assert drop_in.is_dir()  # the ICMP fallback names this unit (ICMP-012)


def test_api_unit_joins_the_target_hardened_and_disabled() -> None:
    """6.2, 15.3, SVC-009, PKG-003: vakr user, no capability, read-only but state and socket.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    api = unit("vakr-api.service")
    settings = dict(api["Service"])
    assert settings["ExecStart"] == "/opt/vakr/venv/bin/vakr api serve"
    assert (settings["Type"], settings["User"], settings["Group"]) == ("notify", "vakr", "vakr")
    assert settings["RestartPreventExitStatus"] == "78"
    assert "ExecReload" not in settings  # every [api] option applies at restart
    for key, value in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"),
                       ("ReadWritePaths", "/var/lib/vakr /run/vakr"), ("ProtectHome", "yes"),
                       ("PrivateTmp", "yes"), ("CapabilityBoundingSet", "")):  # fmt: skip
        assert settings[key] == value, key
    assert "AF_NETLINK" not in settings["RestrictAddressFamilies"]
    assert "RuntimeDirectory" not in settings  # tmpfiles owns /run/vakr; stop must not delete it
    assert ("PartOf", "vakr.target") in api["Unit"]
    assert api["Install"] == [("WantedBy", "vakr.target")]
    prerm = (PKG / "scripts/deb/prerm").read_text(encoding="utf-8")
    assert "vakr-api.service" in prerm


def test_target_is_the_common_contract() -> None:
    """Section 6.2: vakr.target hangs off multi-user.target and is not auto-enabled.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If the target's install wiring changes.
    """
    assert unit("vakr.target")["Install"] == [("WantedBy", "multi-user.target")]
    postinst = (PKG / "scripts/deb/postinst").read_text(encoding="utf-8")
    assert "systemctl enable" not in postinst


def test_sysusers_defines_vakr_and_its_groups() -> None:
    """PKG-003, E.12: user vakr (home /var/lib/vakr, nologin); groups vakr-admin and vakr-view.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If an account definition differs.
    """
    rows = table(PKG / "sysusers.d/vakr.conf")
    assert ["u", "vakr", "-", "vakr network monitor", "/var/lib/vakr", "/usr/sbin/nologin"] in rows
    assert ["g", "vakr-admin", "-", "-", "-", "-"] in rows
    assert ["g", "vakr-view", "-", "-", "-", "-"] in rows


def test_tmpfiles_modes_match_the_layout() -> None:
    """PKG-002/PKG-003 and section 11.1: directory owners and modes.

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If any directory's mode or owner differs.
    """
    rows = {r[1]: (r[0], r[2], r[3], r[4]) for r in table(PKG / "tmpfiles.d/vakr.conf")}
    assert rows == {
        "/run/vakr": ("d", "0751", "vakr", "vakr"),  # the API socket is reachable (E.30)
        "/var/lib/vakr": ("d", "0750", "vakr", "vakr"),
        "/var/cache/vakr": ("d", "0750", "root", "vakr"),
        "/etc/vakr": ("d", "0755", "root", "vakr"),
        "/etc/vakr/secrets": ("d", "0750", "root", "vakr"),
        "/etc/vakr/tls": ("d", "0755", "root", "root"),
        "/etc/vakr/tls/ca.d": ("d", "0755", "root", "root"),
    }
