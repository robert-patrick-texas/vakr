"""Test-wide fixtures: keep host ping permission out of tests that are not about ICMP.

A host that cannot ping makes the ``icmp`` health check CRITICAL (ICMP-012,
a hard stop). CI runners often cannot ping, so tests that judge overall
health (``vakr doctor``, ``vakr check health``) would fail there for a
reason unrelated to what they test. On such a host, and only there, the
registered ``icmp`` check is replaced by a neutral one. The ICMP check's own
tests call it directly and still exercise it on every host.

Dependencies:
    pytest; the ICMP socket probes and the health registry.

Example:
    $ uv run pytest tests/unit/test_cli.py
"""

import pytest

from vakr.adapters.icmp.health import raw_socket_problem, socket_problem
from vakr.health import registry
from vakr.health.model import CheckResult, Status

HOST_CANNOT_PING = socket_problem() is not None and raw_socket_problem() is not None


def check_icmp() -> CheckResult:
    """Stand in for the ICMP check on a test host that cannot ping.

    Args:
        None

    Returns:
        CheckResult: OK, saying the real check was skipped.

    Raises:
        None
    """
    return CheckResult("icmp", Status.OK, "not checked: this test host cannot ping")


@pytest.fixture(autouse=True)
def _neutral_icmp_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the registered ICMP check on hosts that cannot ping.

    Args:
        monkeypatch: Swaps the registry entry.

    Returns:
        None

    Raises:
        None
    """
    if HOST_CANNOT_PING:
        real = registry.check_icmp
        checks = tuple(check_icmp if c is real else c for c in registry.CHECKS)
        monkeypatch.setattr(registry, "CHECKS", checks)
