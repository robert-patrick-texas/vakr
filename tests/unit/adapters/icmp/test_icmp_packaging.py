"""Static half of ICMP-T07: raw-socket capability stays with the collector only.

By default no vakr unit receives ``CAP_NET_RAW`` (ICMP-012, 15.3). The one
documented exception is an example drop-in for ``vakr-collector.service``
that operators install by hand, and the package must not ship it. The
runtime half of ICMP-T07 (the API process cannot open a raw socket) needs
the collector and API units, which arrive with the service milestone.

Dependencies:
    configparser, pathlib: Read the drop-in and the package spec.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_packaging.py
"""

import configparser
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
EXAMPLE = REPO / "packaging/examples/vakr-collector.service.d/50-icmp-cap-net-raw.conf"


def test_icmp_t07_example_dropin_grants_only_cap_net_raw_to_the_collector() -> None:
    """ICMP-T07: the fallback names the collector unit and grants nothing else.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    parser = configparser.ConfigParser(strict=True, interpolation=None)
    parser.read_string(EXAMPLE.read_text(encoding="utf-8"))
    assert parser.sections() == ["Service"]
    assert dict(parser["Service"]) == {
        "ambientcapabilities": "CAP_NET_RAW",  # configparser lower-cases keys
        "capabilityboundingset": "CAP_NET_RAW",
    }
    assert EXAMPLE.parent.name == "vakr-collector.service.d"
    assert "vakr-api.service" in EXAMPLE.read_text(encoding="utf-8")  # the warning is there


def test_icmp_t07_package_ships_no_raw_socket_capability() -> None:
    """ICMP-T07: the example is not packaged, and no packaged unit grants CAP_NET_RAW.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    spec = (REPO / "packaging/nfpm.yaml").read_text(encoding="utf-8")
    assert "examples" not in spec
    for unit in sorted((REPO / "packaging/systemd").iterdir()):
        text = unit.read_text(encoding="utf-8")
        assert "CAP_NET_RAW" not in text, unit.name
        assert "AmbientCapabilities" not in text, unit.name
