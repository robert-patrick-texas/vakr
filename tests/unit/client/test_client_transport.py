"""One bounded exchange over the socket: each failure says what happened and what to run (13.4).

Each test uses a real Unix socket: none at all, a stale file nothing
listens on, a server that never answers or trickles bytes, one that sends
garbage or an oversized body. Every failure is ``SourceUnavailableError``,
ends with the ``systemctl``/``vakr doctor``/``--local`` hint, and arrives
within the E.29 bound, never later.

Dependencies:
    pytest; tests.unit.client.client_testkit.

Example:
    $ uv run pytest tests/unit/client/test_client_transport.py
"""

import socket
import time
from pathlib import Path

import pytest

from tests.unit.client.client_testkit import (
    ROOT,
    Reply,
    canned,
    http,
    raw_server,
    short_dir,
    silent,
    trickle,
)
from vakr.client import transport
from vakr.client.transport import HINT, Limits, explain, get
from vakr.query import SourceUnavailableError

QUICK = Limits(connect_s=0.5, request_s=0.3)


def failure(path: Path, limits: Limits | None = None) -> tuple[str, float]:
    """Make one exchange that must fail; return the message and the seconds it took.

    Args:
        path: The socket.
        limits: The bounds.

    Returns:
        tuple[str, float]: The message and the elapsed time.

    Raises:
        AssertionError: If the exchange succeeds.
    """
    started = time.monotonic()
    with pytest.raises(SourceUnavailableError) as exc:
        get(str(path), "/api/v1/health/live", limits)
    return str(exc.value), time.monotonic() - started


def test_13_4_no_socket_and_a_stale_socket_say_what_to_run() -> None:
    """13.4: a missing socket and one nothing listens on (a crash) are told apart.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with short_dir() as d:
        gone, _ = failure(d / "api.sock")
        assert gone == f"vakr-api is not running: {d / 'api.sock'} is gone; {HINT}"
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(d / "stale.sock"))  # bound, never listening: what a crash leaves
        try:
            refused, _ = failure(d / "stale.sock")
        finally:
            stale.close()
    assert refused.startswith(f"nothing answers on {d / 'stale.sock'}: vakr-api stopped uncleanly")
    assert "systemctl status vakr-api" in refused
    assert "vakr doctor" in refused
    assert refused.endswith("or add --local to read the databases directly")


@pytest.mark.parametrize("reply", [silent, trickle], ids=["silent", "trickle"])
def test_e29_a_slow_server_is_cut_at_the_whole_exchange_bound(reply: Reply) -> None:
    """E.29: silence and a byte-by-byte trickle both end at ``request_s``, not later.

    Args:
        reply: How the server misbehaves.

    Returns:
        None

    Raises:
        None
    """
    with short_dir() as d, raw_server(d / "api.sock", reply):
        message, took = failure(d / "api.sock", QUICK)
    assert message.startswith(f"vakr-api did not answer on {d / 'api.sock'} within 0.3 s")
    assert 0.25 < took < 2.0


def test_e29_oversized_answers_are_refused_declared_or_chunked() -> None:
    """E.29: a body over ``max_bytes`` is refused, whether its length is declared or not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    small = Limits(max_bytes=10)
    chunked = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n40\r\n"
        + b"x" * 64
        + b"\r\n0\r\n\r\n"
    )
    for answer in (http(200, b"x" * 11), http(200, b"", "Content-Length: 99999999"), chunked):
        with short_dir() as d, raw_server(d / "api.sock", canned(answer)):
            message, _ = failure(d / "api.sock", small)
        assert "sent an answer larger than 10 bytes" in message
        assert "Appendix E.29" in message


def test_garbage_and_a_good_answer() -> None:
    """A reply that is not HTTP is unusable; a good one comes back whole, with its status.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with short_dir() as d, raw_server(d / "api.sock", canned(b"not http at all\r\n\r\n")):
        message, _ = failure(d / "api.sock", QUICK)
    assert message.startswith("vakr-api gave no usable answer on")
    assert "(BadStatusLine)" in message
    with short_dir() as d, raw_server(d / "api.sock", canned(http(404, b'{"code": "x"}'))):
        answer = get(str(d / "api.sock"), "/api/v1/health/live")
    assert (answer.status, answer.body) == (404, b'{"code": "x"}')
    assert answer.elapsed_s < transport.REQUEST_TIMEOUT_S


@pytest.mark.skipif(ROOT, reason="root may connect to any socket")
def test_a_socket_this_account_may_not_open() -> None:
    """A socket without write permission for this account names the groups to join.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with short_dir() as d, raw_server(d / "api.sock", silent):
        (d / "api.sock").chmod(0)
        message, _ = failure(d / "api.sock", QUICK)
    assert message.startswith(f"permission denied on {d / 'api.sock'}; run as root or as a member")


def test_connect_timeouts_name_the_connect_bound() -> None:
    """A connect that times out names ``connect_s``; the watchdog names ``request_s``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    limits = Limits(connect_s=2.0, request_s=30.0)
    assert explain(TimeoutError(), "/run/vakr/api.sock", limits, False).startswith(
        "vakr-api did not accept a connection on /run/vakr/api.sock within 2 s"
    )
    assert explain(OSError(), "/s", limits, True).startswith(
        "vakr-api did not answer on /s within 30 s"
    )
