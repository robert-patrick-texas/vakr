"""AUTH-006, SEC-013: forwarded headers count only from trusted proxies.

Behind a trusted proxy, the client address and scheme come from
``Forwarded`` or ``X-Forwarded-For``/``-Proto``, read from the nearest hop
outwards; from anyone else those headers change nothing. The effective
scheme decides the cookie's name and ``Secure`` flag, and whether a login
over plain HTTP is refused.

Dependencies:
    vakr.api.proxy; tests.unit.api.api_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_proxy.py
"""

import ipaddress

import pytest

from tests.unit.api.api_testkit import PROXY, REMOTE, ApiKit
from tests.unit.auth.auth_testkit import new_password
from vakr.api.proxy import parse_address, resolve_client
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role

TRUSTED = (ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("10.9.0.0/16"))


@pytest.mark.parametrize(
    ("peer", "headers", "address", "scheme"),
    [
        ("127.0.0.1", {"x-forwarded-for": "203.0.113.5", "x-forwarded-proto": "https"},
         "203.0.113.5", "https"),
        ("203.0.113.9", {"x-forwarded-for": "10.0.0.1", "x-forwarded-proto": "https"},
         "203.0.113.9", "http"),  # untrusted peer: ignored
        ("127.0.0.1", {"x-forwarded-for": "6.6.6.6, 203.0.113.5"}, "203.0.113.5", "http"),
        ("127.0.0.1", {"x-forwarded-for": "203.0.113.5, 10.9.1.1"}, "203.0.113.5", "http"),
        ("127.0.0.1", {"forwarded": 'for=192.0.2.60;proto=https, for="[2001:db8::7]:4711"'},
         "2001:db8::7", "http"),
        ("127.0.0.1", {"forwarded": "for=192.0.2.60;proto=https;by=10.9.0.1, for=10.9.0.2"},
         "192.0.2.60", "https"),
        ("127.0.0.1", {"forwarded": "for=unknown;proto=https"}, "127.0.0.1", "https"),
        ("127.0.0.1", {"forwarded": "for=127.0.0.1;proto=https", "x-forwarded-for": "6.6.6.6"},
         "6.6.6.6", "http"),  # nginx sets X-Forwarded-For; a client's Forwarded must not win
        ("127.0.0.1", {"x-forwarded-for": "not-an-ip", "x-forwarded-proto": "gopher"},
         "127.0.0.1", "http"),
        ("127.0.0.1", {}, "127.0.0.1", "http"),
        ("garbage", {"x-forwarded-proto": "https"}, "unknown", "http"),
    ],
)  # fmt: skip
def test_auth006_forwarded_headers_follow_trust(
    peer: str, headers: dict[str, str], address: str, scheme: str
) -> None:
    """AUTH-006: the walk stops at the first untrusted or unparseable hop.

    Args:
        peer: Connecting address.
        headers: Forwarded headers.
        address: Expected client address.
        scheme: Expected scheme.

    Returns:
        None

    Raises:
        None
    """
    view = resolve_client(peer, "http", headers, TRUSTED)
    assert (view.address, view.scheme) == (address, scheme)


def test_address_parsing_and_loopback_flags() -> None:
    """SEC-013: addresses with ports and brackets parse; names and junk do not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert str(parse_address('"[2001:db8::1]:443"')) == "2001:db8::1"
    assert str(parse_address("192.0.2.1:8080")) == "192.0.2.1"
    assert parse_address("_hidden") is None
    assert parse_address(None) is None
    assert resolve_client("::1", "http", {}, ()).loopback
    spoof = resolve_client("198.51.100.1", "http", {"x-forwarded-for": "127.0.0.1"}, ())
    assert not spoof.loopback
    assert spoof.ignored


def test_auth006_cookie_flags_follow_the_trusted_scheme() -> None:
    """AUTH-006, SEC-010: HTTPS reported by a trusted proxy gives the Secure ``__Host-`` cookie.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(proxies=("127.0.0.1/32",))
    kit.add_user("alice", Role.OPERATOR)
    proxied = kit.client(peer=PROXY, headers={"X-Forwarded-For": REMOTE,
                                              "X-Forwarded-Proto": "https"})  # fmt: skip
    answer = kit.login(proxied, "alice")
    assert answer.status_code == 200
    cookie = answer.headers["set-cookie"]
    assert cookie.startswith("__Host-vakr_session=")
    assert "Secure" in cookie
    plain = kit.client(peer=PROXY, headers={"X-Forwarded-For": REMOTE,
                                            "X-Forwarded-Proto": "http"})  # fmt: skip
    refused = kit.login(plain, "alice")
    assert (refused.status_code, refused.json()["code"]) == (403, "insecure_transport")
    with kit.fleet.uow() as uow:
        text = "\n".join(repr(e) for e in uow.audit.list_page(PageRequest(limit=50)).items)
    assert REMOTE in text  # the audit names the real client, not the proxy


def test_auth006_spoofed_headers_from_an_untrusted_peer_change_nothing() -> None:
    """AUTH-006: a remote client claiming HTTPS, loopback, or a request ID gets none of it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()  # trusts no proxy
    kit.add_user("alice", Role.OPERATOR)
    spoof = kit.client(peer=REMOTE, headers={"X-Forwarded-For": "127.0.0.1",
                                             "X-Forwarded-Proto": "https",
                                             "X-Request-ID": "chosen-by-attacker"})  # fmt: skip
    answer = kit.login(spoof, "alice")
    assert (answer.status_code, answer.json()["code"]) == (403, "insecure_transport")
    assert answer.headers["x-request-id"] != "chosen-by-attacker"
    trusted = ApiKit.create(proxies=("127.0.0.1/32",))
    echoed = trusted.client(headers={"X-Forwarded-For": REMOTE, "X-Request-ID": "proxy-abc.1"})
    assert echoed.get("/api/v1/health/live").headers["x-request-id"] == "proxy-abc.1"
    junk = trusted.client(headers={"X-Forwarded-For": REMOTE, "X-Request-ID": "a b\tc"})
    assert junk.get("/api/v1/health/live").headers["x-request-id"] != "a b\tc"


def test_auth006_throttling_is_per_forwarded_client() -> None:
    """AUTH-004, AUTH-006: behind the proxy, one client's failures do not slow another down.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create(proxies=("127.0.0.1/32",))
    kit.add_user("alice", Role.OPERATOR)
    wrong = {"username": "alice", "password": "wrong-" + new_password()}
    attacker = kit.client(headers={"X-Forwarded-For": "203.0.113.66",
                                   "X-Forwarded-Proto": "https"})  # fmt: skip
    for _ in range(7):
        attacker.post("/api/v1/auth/login", json=wrong)
    assert attacker.post("/api/v1/auth/login", json=wrong).status_code == 429
    alice = kit.client(headers={"X-Forwarded-For": REMOTE, "X-Forwarded-Proto": "https"})
    assert kit.login(alice, "alice").status_code == 200
