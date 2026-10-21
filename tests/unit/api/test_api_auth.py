"""Sign-in over HTTP: cookies, rotation, expiry, throttling, audit (AUTH-002, AUTH-004, SEC-018).

Dependencies:
    tests.unit.api.api_testkit: The app over memory stores with a manual clock.

Example:
    $ uv run pytest tests/unit/api/test_api_auth.py
"""

from datetime import timedelta

from tests.unit.api.api_testkit import REMOTE, ApiKit
from tests.unit.auth.auth_testkit import new_password
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role

LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/auth/session"


def audit(kit: ApiKit) -> list[tuple[str, str, str, str | None]]:
    """Return ``(action, outcome, actor, request_id)`` of each audit event, oldest first.

    Args:
        kit: The app.

    Returns:
        list: The events.

    Raises:
        None
    """
    with kit.fleet.uow() as uow:
        page = uow.audit.list_page(PageRequest(limit=500))
    return [(e.action, e.outcome.value, e.actor, e.request_id) for e in reversed(page.items)]


def test_auth002_login_sets_a_strict_cookie_and_rotates_the_session() -> None:
    """AUTH-002, SEC-010: HttpOnly, SameSite=Lax, Path=/, bounded Max-Age; login rotates.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("alice", Role.OPERATOR)
    client = kit.client()
    first = kit.login(client, "alice")
    assert first.status_code == 200
    cookie = first.headers["set-cookie"]
    for flag in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=43200"):
        assert flag in cookie
    assert "Secure" not in cookie
    assert cookie.startswith("vakr_session=vks_")
    old = client.cookies["vakr_session"]
    second = kit.login(client, "alice")  # presents the first cookie: rotation
    assert second.status_code == 200
    assert client.cookies["vakr_session"] != old
    stale = kit.client()
    stale.cookies.set("vakr_session", old)
    assert stale.get(SESSION).status_code == 401  # the replaced session ended
    assert client.get(SESSION).json()["name"] == "alice"


def test_auth002_idle_and_absolute_expiry_and_logout_revocation() -> None:
    """AUTH-002: 30 minutes idle and 12 hours absolute end a session; logout ends it now.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("alice", Role.OPERATOR)
    idle = kit.client()
    kit.login(idle, "alice")
    kit.fleet.clock.advance(timedelta(minutes=31))
    assert idle.get(SESSION).status_code == 401
    busy = kit.client()
    kit.login(busy, "alice")
    for _ in range(25):  # active every 29 minutes for more than 12 hours
        kit.fleet.clock.advance(timedelta(minutes=29))
        if busy.get(SESSION).status_code != 200:
            break
    assert busy.get(SESSION).status_code == 401
    out = kit.client()
    kit.login(out, "alice")
    token = out.get(SESSION).json()["csrf_token"]
    kept = out.cookies["vakr_session"]
    done = out.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token})
    assert done.status_code == 204
    assert "Max-Age=0" in done.headers["set-cookie"]
    replay = kit.client()
    replay.cookies.set("vakr_session", kept)
    assert replay.get(SESSION).status_code == 401
    assert ("auth.logout", "success", "alice") in [a[:3] for a in audit(kit)]


def test_auth004_throttling_is_progressive_uniform_and_says_retry_after() -> None:
    """AUTH-004, SEC-012: delays from the 5th failure, 429 with Retry-After, unknown = known.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("alice", Role.ADMIN)
    client = kit.client()
    wrong = {"username": "alice", "password": "wrong-" + new_password()}
    answers = []
    for _ in range(5):
        answers.append(client.post(LOGIN, json=wrong))
        kit.fleet.clock.advance(timedelta(seconds=60))  # outlast each wait
    assert [r.status_code for r in answers] == [401] * 5
    assert kit.sleeps == [0.0, 0.0, 0.0, 0.0, 1.0]  # the 5th failure waits 1 s (E.12)
    assert {r.json()["code"] for r in answers} == {"login_failed"}
    client.post(LOGIN, json=wrong)  # 6th failure: the next attempt must wait 2 s
    throttled = client.post(LOGIN, json=wrong)
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"] == "2"
    assert throttled.json()["code"] == "throttled"
    ghost = kit.client().post(LOGIN, json={"username": "ghost", "password": new_password()})
    assert (ghost.status_code, ghost.json()["message"]) == (401, answers[0].json()["message"])
    kit.fleet.clock.advance(timedelta(seconds=60))
    assert kit.login(client, "alice").status_code == 200  # a success clears the run


def test_sec018_login_audit_names_the_client_and_the_request() -> None:
    """SEC-018: login and failed login are audited with the address and request ID, no password.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    password = kit.add_user("alice", Role.OPERATOR)
    client = kit.client()
    ok = kit.login(client, "alice")
    typo = "wrong-" + new_password()
    bad = client.post(LOGIN, json={"username": "alice", "password": typo})
    events = audit(kit)
    logins = [e for e in events if e[0] == "auth.login"]
    assert logins[0][1:] == ("success", "alice", ok.headers["x-request-id"])
    assert logins[1][1:] == ("failure", "alice", bad.headers["x-request-id"])
    with kit.fleet.uow() as uow:
        text = "\n".join(repr(e) for e in uow.audit.list_page(PageRequest(limit=500)).items)
    assert "127.0.0.1" in text
    assert password not in text
    assert typo not in text


def test_login_needs_json_tls_beyond_loopback_and_no_socket() -> None:
    """SEC-011, SEC-013: JSON only (415); plain HTTP from a remote client refused; socket refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("alice", Role.OPERATOR)
    as_form = {"Content-Type": "application/x-www-form-urlencoded"}
    form = kit.client().post(LOGIN, content=b"username=alice", headers=as_form)
    assert (form.status_code, form.json()["code"]) == (415, "unsupported_media_type")
    remote = kit.login(kit.client(peer=REMOTE), "alice")
    assert (remote.status_code, remote.json()["code"]) == (403, "insecure_transport")
    assert "set-cookie" not in remote.headers
    tls = kit.login(kit.client(peer=REMOTE, scheme="https"), "alice")
    assert tls.status_code == 200
    assert tls.headers["set-cookie"].startswith("__Host-vakr_session=")
    assert "Secure" in tls.headers["set-cookie"]
    body = {"username": "alice", "password": new_password()}
    local = kit.socket_client(Role.ADMIN).post(LOGIN, json=body)
    assert (local.status_code, local.json()["code"]) == (400, "local_socket")
    extra = kit.client().post(LOGIN, json={**body, "admin": True})
    assert extra.status_code == 422


def test_a_cookie_of_the_other_scheme_is_not_accepted() -> None:
    """SEC-010: the plain-HTTP cookie never stands in for the secure one, nor the reverse.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    kit = ApiKit.create()
    kit.add_user("alice", Role.OPERATOR)
    plain = kit.client()
    kit.login(plain, "alice")
    token = plain.cookies["vakr_session"]
    secure = kit.client(peer=REMOTE, scheme="https")
    secure.cookies.set("vakr_session", token)
    assert secure.get(SESSION).status_code == 401
    secure.cookies.clear()
    secure.cookies.set("__Host-vakr_session", token)
    assert secure.get(SESSION).status_code == 200
    oversized = kit.client()
    oversized.cookies.set("vakr_session", "vks_" + "a" * 500)
    assert oversized.get(SESSION).status_code == 401


def test_login_gate_refuses_a_flood_beyond_its_queue() -> None:
    """SEC-012: the global login gate admits a bounded number and refuses the rest with 429.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    import asyncio

    from vakr.api.context import LoginGate
    from vakr.api.errors import ApiError

    async def scenario() -> tuple[int, int]:
        """Hold every slot and every queue place, then try once more.

        Args:
            None

        Returns:
            tuple[int, int]: The refusal's status and how many got in.

        Raises:
            None
        """
        gate, release, entered = LoginGate(active=1, waiting=1), asyncio.Event(), []

        async def hold() -> None:
            """Take a slot and keep it until released.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """
            async with gate.slot():
                entered.append(1)
                await release.wait()

        tasks = [asyncio.create_task(hold()) for _ in range(2)]
        await asyncio.sleep(0)
        try:
            async with gate.slot():
                status = 0
        except ApiError as exc:
            status = exc.status
        release.set()
        await asyncio.gather(*tasks)
        return status, len(entered)

    assert asyncio.run(scenario()) == (429, 2)
