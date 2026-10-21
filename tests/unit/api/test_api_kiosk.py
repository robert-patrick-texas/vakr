"""Kiosk displays over HTTP: the token-for-cookie exchange and its protections (UI-013, E.30 #17).

A display's browser posts its kiosk token once to ``POST /api/v1/auth/kiosk``
and holds an ``HttpOnly``, ``SameSite=Strict`` cookie from then on. The
cookie is checked on every request, so a revoked or expired token signs
the display out at its next request; a person's session cookie in the
same browser wins over it; plain HTTP beyond loopback, cross-site
requests, and non-JSON bodies are refused, as for sign-in.

Dependencies:
    tests.unit.api.api_testkit: The app over memory stores with a manual clock.

Example:
    $ uv run pytest tests/unit/api/test_api_kiosk.py
"""

import logging
import secrets
from datetime import timedelta

import pytest

from tests.unit.api.api_testkit import REMOTE, ApiKit
from vakr.api.context import LoginGate
from vakr.domain.users import Role

KIOSK = "/api/v1/auth/kiosk"
FORGET = "/api/v1/auth/kiosk/forget"
SESSION = "/api/v1/auth/session"


def test_ui013_exchange_sets_a_strict_kiosk_cookie_for_an_operator_display() -> None:
    """UI-013, SEC-010: HttpOnly, SameSite=Strict, Path=/, Max-Age = the token's remaining life.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    token = kit.kiosk_token("noc-wall")
    kit.fleet.clock.advance(timedelta(days=1))
    client = kit.client()
    answer = client.post(KIOSK, json={"token": token})
    assert answer.status_code == 200
    cookie = answer.headers["set-cookie"]
    for flag in ("HttpOnly", "SameSite=strict", "Path=/", f"Max-Age={29 * 86400}"):
        assert flag in cookie
    assert cookie.startswith("vakr_kiosk=vkk_")
    assert "Secure" not in cookie
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json()["kind"] == "kiosk"
    who = client.get(SESSION).json()
    assert (who["kind"], who["name"], who["role"]) == ("kiosk", "kiosk:noc-wall", "operator")
    assert who["expires_at"] is not None
    assert who["csrf_token"] is None
    assert client.get("/api/v1/devices").status_code == 200
    assert client.get("/api/v1/kiosk-tokens").status_code == 403  # operator: no admin reads


def test_e30_17_https_gets_the_secure_cookie_and_remote_plain_http_none() -> None:
    """E.30 #3, #4, #17: the cookie name and Secure follow the scheme; remote plain HTTP is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    token = kit.kiosk_token()
    tls = kit.client(peer=REMOTE, scheme="https").post(KIOSK, json={"token": token})
    assert tls.status_code == 200
    assert tls.headers["set-cookie"].startswith("__Host-vakr_kiosk=")
    assert "Secure" in tls.headers["set-cookie"]
    plain = kit.client(peer=REMOTE).post(KIOSK, json={"token": token})
    assert (plain.status_code, plain.json()["code"]) == (403, "insecure_transport")
    assert "set-cookie" not in plain.headers
    secure_only = kit.client(peer=REMOTE)
    secure_only.cookies.set("__Host-vakr_kiosk", token)
    assert secure_only.get(SESSION).status_code == 401  # each scheme reads only its own name


def test_e30_5_exchange_refuses_cross_site_non_json_and_the_socket() -> None:
    """E.30 #5, #17: the protections of sign-in apply to the exchange.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    token = kit.kiosk_token()
    client = kit.client()
    cross = client.post(KIOSK, json={"token": token}, headers={"Sec-Fetch-Site": "cross-site"})
    assert (cross.status_code, cross.json()["code"]) == (403, "cross_site")
    origin = client.post(KIOSK, json={"token": token}, headers={"Origin": "https://evil.test"})
    assert origin.status_code == 403
    form = client.post(KIOSK, content=f"token={token}",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})  # fmt: skip
    assert form.status_code == 415
    socket = kit.socket_client(Role.ADMIN).post(KIOSK, json={"token": token})
    assert (socket.status_code, socket.json()["code"]) == (400, "local_socket")
    assert "set-cookie" not in cross.headers
    assert "set-cookie" not in form.headers


@pytest.mark.parametrize("kind", ["unknown", "session", "garbage", "long"])
def test_e30_17_a_refused_token_is_401_after_the_fixed_delay(kind: str) -> None:
    """E.30 #17: anything but a live kiosk token is refused the same way, never echoed.

    Args:
        kind: What is presented.

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    presented = {
        "unknown": "vkk_" + secrets.token_urlsafe(32),
        "session": "vks_" + secrets.token_urlsafe(32),
        "garbage": "not a token",
        "long": "vkk_" + "x" * 200,
    }[kind]
    answer = kit.client().post(KIOSK, json={"token": presented})
    assert answer.status_code == (422 if kind == "long" else 401)
    assert presented not in answer.text
    if kind != "long":
        assert answer.json()["code"] == "kiosk_refused"
        assert kit.sleeps == [1.0]


def test_e12_revocation_and_expiry_sign_the_display_out_at_its_next_request() -> None:
    """E.12, SEC-010: the cookie is checked every time; revoked or expired means 401 at once.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    client = kit.client()
    assert client.post(KIOSK, json={"token": kit.kiosk_token("wall-a")}).status_code == 200
    record = kit.auth.kiosk.find("wall-a")
    assert record is not None
    kit.auth.kiosk.revoke(record.id, actor="test")
    assert client.get("/api/v1/devices").status_code == 401
    later = kit.client()
    assert later.post(KIOSK, json={"token": kit.kiosk_token("wall-b")}).status_code == 200
    kit.fleet.clock.advance(timedelta(days=30))
    assert later.get("/api/v1/devices").status_code == 401


def test_e30_17_a_session_in_the_same_browser_wins_and_forget_clears_the_cookie() -> None:
    """E.30 #17: a person signed in outranks the display; forget drops only the kiosk cookie.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("olivia", Role.OPERATOR)
    client = kit.client()
    assert client.post(KIOSK, json={"token": kit.kiosk_token()}).status_code == 200
    assert kit.login(client, "olivia").status_code == 200
    assert client.get(SESSION).json()["kind"] == "user"
    csrf = client.get(SESSION).json()["csrf_token"]
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert client.get(SESSION).json()["kind"] == "kiosk"  # the display again
    gone = client.post(FORGET)
    assert gone.status_code == 204
    assert "Max-Age=0" in gone.headers["set-cookie"]
    assert gone.headers["set-cookie"].startswith("vakr_kiosk=")
    assert client.get(SESSION).status_code == 401
    cross = kit.client().post(FORGET, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403


def test_e30_17_exchanges_have_their_own_bounded_gate() -> None:
    """E.30 #17: a full exchange gate answers 429 at once; password logins are not affected.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    token = kit.kiosk_token()
    kit.context.kiosk_exchanges = LoginGate(active=1, waiting=0)
    kit.context.kiosk_exchanges._inside = 1  # one exchange already in progress
    busy = kit.client().post(KIOSK, json={"token": token})
    assert (busy.status_code, busy.headers["retry-after"]) == (429, "5")
    assert kit.context.logins is not kit.context.kiosk_exchanges


def test_sec018_setup_is_audited_and_the_token_reaches_no_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SEC-018: setup is audited with the label and client address; the token is logged nowhere.

    Args:
        caplog: Log capture.

    Returns:
        None

    Raises:
        None
    """
    caplog.set_level(logging.DEBUG)
    kit = ApiKit.create()
    token = kit.kiosk_token("lobby")
    kit.client().post(KIOSK, json={"token": token})
    kit.client().post(KIOSK, json={"token": token + "x"})
    with kit.fleet.uow() as uow:
        from vakr.domain.paging import PageRequest

        events = list(reversed(uow.audit.list_page(PageRequest(limit=500)).items))
    setup = [e for e in events if e.action == "kiosk.setup"]
    assert len(setup) == 1
    assert setup[0].actor == "kiosk:lobby"
    assert "127.0.0.1" in setup[0].summary
    assert token not in "\n".join(repr(e) for e in events)
    assert token not in caplog.text
    assert "api.kiosk_refused" in {getattr(r, "event_id", "") for r in caplog.records}
