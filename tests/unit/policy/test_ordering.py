"""LIFE-007: sticky and primary interfaces first, then display rank, then natural order.

Dependencies:
    vakr.policy.ordering: The display order under test.

Example:
    $ uv run pytest tests/unit/policy/test_ordering.py
"""

from tests.unit.policy.policy_fixtures import defaults, edge, rules
from vakr.policy.engine import decide
from vakr.policy.facts import Annotations
from vakr.policy.ordering import display_order, natural_key


def test_natural_order_compares_numbers_as_numbers() -> None:
    """``Gi0/0/2`` sorts before ``Gi0/0/10``; text compares case-insensitively.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    names = ["GigabitEthernet0/0/10", "gigabitethernet0/0/2", "GigabitEthernet0/0/1"]
    assert sorted(names, key=natural_key) == [
        "GigabitEthernet0/0/1",
        "gigabitethernet0/0/2",
        "GigabitEthernet0/0/10",
    ]


def test_device_page_order() -> None:
    """LIFE-007: sticky, then primary, then rank (unranked last), then natural order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules({"name": "pin-uplink", "devices": ["edge-rtr1"], "interfaces": ["Te*"],
                    "annotations": {"sticky": True}})  # fmt: skip
    interfaces = [
        edge("Vlan100", if_index=20),
        edge("GigabitEthernet0/0/10", "", if_index=12),
        edge("GigabitEthernet0/0/2", "", if_index=4),
        edge("Loopback0", if_index=30, annotations=Annotations(display_rank=1)),
        edge("GigabitEthernet0/0/0", if_index=1),  # ISP: primary
        edge("Port-channel1", if_index=40),  # UPLINK: primary
        edge("TenGigabitEthernet1/1/1", if_index=50),  # sticky by rule
        edge("GigabitEthernet0/0/1", if_index=2, annotations=Annotations(display_rank=5)),
        edge("Tunnel10", if_index=60, annotations=Annotations(sticky=True, display_rank=9)),
    ]
    ordered = display_order((f, decide(f, policy, defaults())) for f in interfaces)
    assert [facts.if_name for facts, _ in ordered] == [
        "Tunnel10",  # sticky, ranked
        "TenGigabitEthernet1/1/1",  # sticky, unranked
        "GigabitEthernet0/0/1",  # primary (WAN keyword), ranked
        "GigabitEthernet0/0/0",  # primary, unranked, natural order
        "Port-channel1",
        "Loopback0",  # secondary, ranked
        "GigabitEthernet0/0/2",  # secondary, natural order: 2 before 10
        "GigabitEthernet0/0/10",
        "Vlan100",
    ]
