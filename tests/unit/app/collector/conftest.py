"""Collector test fixtures: let service tests run on hosts that cannot ping.

The collector refuses to start on a host that cannot send ICMP Echo
(ICMP-012). Service tests that are not about ICMP still need to start it on
a CI runner whose ``ping_group_range`` excludes the test user, so there,
and only there, the startup check is skipped. ``test_col_icmp_hard_stop.py``
proves the check itself on every host.

Dependencies:
    pytest; the ICMP socket probes.

Example:
    $ uv run pytest tests/unit/app/collector
"""

import pytest

from vakr.adapters.icmp.health import raw_socket_problem, socket_problem
from vakr.app.collector import preflight

HOST_CANNOT_PING = socket_problem() is not None and raw_socket_problem() is not None


@pytest.fixture(autouse=True)
def _ping_capability(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the ICMP startup check on a host that cannot ping, unless a test opts out.

    Args:
        request: The test; the ``real_icmp_preflight`` marker keeps the check.
        monkeypatch: Replaces the check.

    Returns:
        None

    Raises:
        None
    """
    if HOST_CANNOT_PING and request.node.get_closest_marker("real_icmp_preflight") is None:
        monkeypatch.setattr(preflight, "check_icmp_capability", lambda: None)
