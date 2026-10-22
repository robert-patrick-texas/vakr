"""Node rules, the token file, the partner client over real TLS, and the peer inventory (MPOS-006).

Dependencies:
    pytest; the lifecycle test PKI and HTTPS server (``conftest``).

Example:
    $ uv run pytest tests/unit/replication/test_replication_parts.py
"""

import json
import os
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.lifecycle.helpers import HttpsServer, Pki, Route
from vakr.app.collector.replication_loop import PeerInventory
from vakr.config.loader import EffectiveConfig
from vakr.config.node import node_problems, node_settings
from vakr.credentials.policy import FilePolicy
from vakr.credentials.secret import Secret
from vakr.lifecycle.egress.client import EgressSettings
from vakr.lifecycle.egress.errors import EgressError, ResponseTooLargeError
from vakr.replication import client as client_module
from vakr.replication.client import PartnerClient, PartnerError
from vakr.replication.token_file import TokenFileError, read_token

STATE = "/api/v1/replication/metadata/state"


def _node(**node: str) -> EffectiveConfig:
    """Build a configuration with a ``[node]`` table.

    Args:
        **node: Its keys.

    Returns:
        EffectiveConfig: The configuration.

    Raises:
        None
    """
    return EffectiveConfig({"node": node})


@pytest.mark.parametrize(("node", "says"), [
    ({"role": "peer", "position": "west"}, "https://"),
    ({"role": "peer", "position": "west", "primary_url": "http://east"}, "https://"),
    ({"role": "peer", "primary_url": "https://east.example"}, "must be set on a peer"),
    ({"role": "primary", "primary_url": "https://east.example"}, "role is primary"),
    ({"position": "east west"}, "node.position"),
    ({"role": "peer", "position": "w", "primary_url": "https://u:p@east.example"}, "no user name"),
])  # fmt: skip
def test_mpos001_node_rules(node: dict[str, str], says: str) -> None:
    """MPOS-001, MPOS-006: a peer names its primary over https; a primary names none.

    Args:
        node: The ``[node]`` table.
        says: Part of the expected problem.

    Returns:
        None

    Raises:
        None
    """
    assert any(says in p for p in node_problems(_node(**node)))


def test_mpos001_a_single_node_is_a_primary_and_unchanged() -> None:
    """MPOS-001: no ``[node]`` table means a primary without a peer, with no refusals.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    single = node_settings(EffectiveConfig({}))
    peer = node_settings(_node(role="peer", position="west", primary_url="https://east.example/"))
    facts = (single.is_peer, single.refusal(), single.header())
    assert facts == (False, None, "")
    assert node_problems(EffectiveConfig({})) == []
    assert peer.primary_url == "https://east.example"
    assert peer.header() == "west (peer of https://east.example)"


def test_mpos006_the_token_file_is_read_strictly(tmp_path: Path) -> None:
    """MPOS-006, SEC-016: one vkr_ token, in a file nobody else can write or read.

    Args:
        tmp_path: Where the file lives.

    Returns:
        None

    Raises:
        None
    """
    policy = FilePolicy(frozenset({os.getuid()}), os.getgid())
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o750)
    path = secrets / "replication.token"
    path.write_text("vkr_" + "A" * 43 + "\n")
    path.chmod(0o640)
    token = read_token(path, policy)
    assert token.reveal() == "vkr_" + "A" * 43
    assert "AAAA" not in repr(token)
    path.chmod(0o644)
    with pytest.raises(TokenFileError, match="replication token create"):
        read_token(path, policy)
    path.chmod(0o640)
    path.write_text("vkk_kiosk\n")
    with pytest.raises(TokenFileError, match="does not hold one replication token"):
        read_token(path, policy)
    with pytest.raises(TokenFileError):
        read_token(secrets / "absent", policy)


def _settings(pki: Pki, *, trusted: bool = True) -> EgressSettings:
    """Build egress settings trusting the test CA (or an unrelated one).

    Args:
        pki: The test PKI.
        trusted: Whether the server's issuer is trusted.

    Returns:
        EgressSettings: The settings.

    Raises:
        None
    """
    ca = pki.ca if trusted else pki.other_ca
    return EgressSettings(ssl.create_default_context(cafile=str(ca.cert_path)))


def test_mpos006_the_client_sends_the_token_over_verified_https_only(
    pki: Pki, https_server: HttpsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MPOS-006, EGRESS-004: verified TLS, no redirects, a bounded body, and envelopes as errors.

    Args:
        pki: The test PKI.
        https_server: A local HTTPS server.
        monkeypatch: Shrinks the size cap.

    Returns:
        None

    Raises:
        None
    """
    token = Secret("vkr_" + "B" * 43)
    https_server.routes[STATE] = Route(200, json.dumps({"digest": "x"}).encode())
    good = PartnerClient(https_server.base, token, _settings(pki))
    assert good.get_json("metadata/state") == {"digest": "x"}
    assert "BBBB" not in repr(good)
    https_server.routes[STATE] = Route(401, b'{"code": "unauthenticated", "message": "no"}')
    with pytest.raises(PartnerError) as refused:
        good.get_json("metadata/state")
    assert (refused.value.status, refused.value.code) == (401, "unauthenticated")
    https_server.routes[STATE] = Route(302, b"", {"Location": "https://elsewhere.example/"})
    with pytest.raises(EgressError):
        good.get_json("metadata/state")
    https_server.routes[STATE] = Route(200, b"{}")
    with pytest.raises(EgressError):
        PartnerClient(https_server.base, token, _settings(pki, trusted=False)).get_json(
            "metadata/state")  # fmt: skip
    monkeypatch.setattr(client_module, "MAX_ANSWER_BYTES", 4)
    https_server.routes[STATE] = Route(200, b'{"too": "large"}')
    with pytest.raises(ResponseTooLargeError):
        PartnerClient(https_server.base, token, _settings(pki)).get_json("metadata/state")
    with pytest.raises(ValueError, match="https"):
        PartnerClient("http://127.0.0.1", token, _settings(pki))


def test_adr024_a_peer_inventory_never_writes_discovery() -> None:
    """ADR-024: discovery on a peer answers the replicated plan and hides the report path.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    plan = object()
    inner = SimpleNamespace(poll_plan=lambda _d: plan, devices=lambda: ("core1",),
                            record_discovery_report=lambda *_a: pytest.fail("wrote"))  # fmt: skip
    peer = PeerInventory(inner)
    assert peer.record_discovery("d", object(), None) is plan
    assert peer.devices() == ("core1",)
    assert not hasattr(peer, "record_discovery_report")
