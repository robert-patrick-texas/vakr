"""Tests for the ``[icmp]`` configuration block (E.1, E.9, E.17, E.22).

The catalog defaults must equal the code defaults and Appendix E, the
latency threshold's 0 must mean "off", and validation must refuse probe
counts that would stop SNMP under ``icmp_gated`` or overflow Compact
storage, and spacing or timeouts outside their bounds.

Dependencies:
    pytest: Temporary configuration directories.
    vakr.config, vakr.collectors.icmp.settings: Loader and code under test.

Example:
    $ uv run pytest tests/unit/collectors/icmp/test_icmp_settings.py
"""

from pathlib import Path

import pytest

from vakr.collectors.icmp.settings import (
    IcmpSettings,
    require_gate_probes,
    settings_from_config,
)
from vakr.config import project
from vakr.config.loader import ConfigError, EffectiveConfig, load_effective_config
from vakr.domain.enums import ReachabilityStrategy
from vakr.logic.icmp import DEFAULT_POLICY
from vakr.ports.icmp import ProbeRequest


def write_config(tmp_path: Path, body: str) -> Path:
    """Write a ``config.toml`` with an ``[icmp]`` table.

    Args:
        tmp_path: Pytest temporary directory.
        body: Lines inside ``[icmp]``.

    Returns:
        Path: The configuration directory.

    Raises:
        None
    """
    (tmp_path / "config.toml").write_text(f"config_version = 1\n[icmp]\n{body}\n")
    return tmp_path


def test_packaged_defaults_are_appendix_e_and_match_the_code() -> None:
    """E.1, E.9: 5 probes, 200 ms, 1 s, loss 40%, latency off; code and catalog agree.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    settings = settings_from_config(EffectiveConfig(project.catalog().defaults()))
    assert settings == IcmpSettings()
    assert (settings.probes, settings.spacing_s, settings.timeout_s) == (5, 0.2, 1.0)
    assert settings.policy == DEFAULT_POLICY
    assert settings.max_in_flight == 1024
    assert settings_from_config(EffectiveConfig({})) == settings


def test_operator_values_are_applied(tmp_path: Path) -> None:
    """CONF-006: operator overrides reach the collector, with the RTT threshold enabled.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    body = 'probes = 3\nspacing = "500ms"\ntimeout = "2s"\nimpaired_rtt_ms = 150\n'
    config = load_effective_config(write_config(tmp_path, body + "impaired_loss_percent = 50"))
    settings = settings_from_config(config)
    assert (settings.probes, settings.spacing_s, settings.timeout_s) == (3, 0.5, 2.0)
    assert (settings.policy.impaired_loss_percent, settings.policy.impaired_rtt_ms) == (50.0, 150.0)
    assert settings.request("192.0.2.1") == ProbeRequest("192.0.2.1", 3, 0.5, 2.0)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        pytest.param("probes = 1", "icmp.probes: must be at least 2", id="E.17 icmp_gated"),
        pytest.param("probes = 6", "icmp.probes: must be at most 5", id="ADR-021 columns"),
        pytest.param('spacing = "5ms"', "icmp.spacing: must be at least 0.02", id="burst"),
        pytest.param('timeout = "10s"', "icmp.timeout: must be at most 5", id="long timeout"),
        pytest.param("impaired_loss_percent = 0", "must be at least 1", id="zero loss"),
        pytest.param("max_in_flight = 100000", "must be at most 16384", id="unbounded"),
    ],
)
def test_validation_refuses_unsafe_icmp_settings(tmp_path: Path, line: str, message: str) -> None:
    """E.17: fewer than 2 probes would stop SNMP; every ICMP value is bounded.

    Args:
        tmp_path: Pytest temporary directory.
        line: One ``[icmp]`` setting.
        message: Expected validation text.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ConfigError, match=message):
        load_effective_config(write_config(tmp_path, line))


def test_icmp_gated_needs_two_probes_but_icmp_only_does_not() -> None:
    """ICMP-006, E.17: the cross-setting rule is also enforced in code.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="SNMP needs 2 replies"):
        require_gate_probes(ReachabilityStrategy.ICMP_GATED, 1)
    require_gate_probes(ReachabilityStrategy.ICMP_GATED, 2)
    require_gate_probes(ReachabilityStrategy.ICMP_ONLY, 1)


def test_unknown_option_is_a_programming_error() -> None:
    """Reading a key the catalog does not define fails loudly instead of guessing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    from vakr.collectors.icmp import settings as module

    with pytest.raises(KeyError, match=r"icmp\.nonexistent"):
        module._value(EffectiveConfig({}), "icmp.nonexistent")
