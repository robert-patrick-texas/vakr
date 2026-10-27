"""A SIGHUP reload reaches a peer's collection policy too (ADR-024, CFG-006).

Configuration files stay node-local on both positions, so an operator who
copies ``policies.toml`` to the peer and reloads must see it applied there
without a restart, just as on the primary.
"""

from pathlib import Path
from types import SimpleNamespace

from vakr.app.collector.inventory_service import reload_service
from vakr.app.collector.replication_loop import PeerInventory
from vakr.config.loader import EffectiveConfig


def test_adr024_a_reload_reaches_the_policy_behind_a_peer_wrapper(tmp_path: Path) -> None:
    """ADR-024: ``reload_service`` swaps the policy of the source a ``PeerInventory`` wraps.

    Args:
        tmp_path: A configuration directory without ``policies.toml`` (the defaults).

    Returns:
        None

    Raises:
        None
    """
    swapped: list[tuple[object, ...]] = []
    inner = SimpleNamespace(replace_policy=lambda *args: swapped.append(args))
    reload_service(PeerInventory(inner), EffectiveConfig({}), tmp_path)
    assert len(swapped) == 1


def test_a_reload_leaves_a_test_fake_alone(tmp_path: Path) -> None:
    """Anything that is neither the service nor a peer wrapper is left as it is.

    Args:
        tmp_path: A configuration directory without ``policies.toml``.

    Returns:
        None

    Raises:
        None
    """
    swapped: list[tuple[object, ...]] = []
    fake = SimpleNamespace(replace_policy=lambda *args: swapped.append(args))
    reload_service(fake, EffectiveConfig({}), tmp_path)  # ty: ignore[invalid-argument-type]
    assert swapped == []
