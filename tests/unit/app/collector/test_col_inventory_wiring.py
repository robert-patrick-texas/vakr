"""The collector polls from the real inventory service (6.2, DISC-001, CONF-003).

``vakr-collector`` builds ``vakr.inventory.service.InventoryService`` behind
its inventory seam, reads collection rules from ``policies.toml``, hands
reloaded rules to the running service on SIGHUP (the change counter moves, so
the scheduler re-plans), and refuses to start on invalid rules with a
preflight error instead of a traceback.

Dependencies:
    pytest; the collector lab's relocated install.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_inventory_wiring.py
"""

import asyncio
from pathlib import Path

import pytest

from tests.integration.collector.collector_lab import make_root
from vakr.app.collector.build import build_parts
from vakr.app.collector.inventory import reload_inventory
from vakr.app.collector.preflight import PreflightError
from vakr.inventory.service import InventoryService

RULE = """
[[interface_policy.rules]]
name = "no-user-ports"
action = "exclude"
match.ifalias = ["exact:", "user port"]
"""


def test_collector_uses_the_inventory_service_and_reloads_its_rules(tmp_path: Path) -> None:
    """6.2, CONF-003: the real service is wired; a reload moves the change counter.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = make_root(tmp_path, 161)
    parts = asyncio.run(build_parts(paths))
    try:
        inventory = parts.inventory
        assert isinstance(inventory, InventoryService)
        assert inventory.devices() == ()  # a fresh install has no devices yet
        before = inventory.change_counter()
        (paths.config_dir / "policies.toml").write_text(RULE, encoding="utf-8")
        reload_inventory(inventory, parts.config, paths.config_dir)
        assert inventory.change_counter() > before
    finally:
        asyncio.run(parts.close())


def test_invalid_policies_fail_preflight(tmp_path: Path) -> None:
    """16.3: broken collection rules stop the start with an operator message.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = make_root(tmp_path, 161)
    (paths.config_dir / "policies.toml").write_text("[[interface_policy.rules]]\n", "utf-8")
    with pytest.raises(PreflightError) as failed:
        asyncio.run(build_parts(paths))
    assert failed.value.check == "policies"
    assert "vakr policy validate" in failed.value.message
