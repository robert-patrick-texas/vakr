"""Service plumbing: sd_notify, signals, journald log lines, the entry point (SVC-004, SVC-006).

Dependencies:
    socket, signal: A real notification socket and real signals.
    vakr.service, vakr.app.collector.entry: The code under test.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_plumbing.py
"""

import asyncio
import io
import logging
import os
import signal
import socket
from pathlib import Path

import pytest

from tests.integration.collector.collector_lab import make_root
from vakr.app.collector import entry
from vakr.credentials import Secret
from vakr.service.logs import JournalFormatter, configure_service_logging
from vakr.service.sdnotify import notify
from vakr.service.signals import service_signals


def test_notify_sends_one_datagram_or_nothing(tmp_path: Path) -> None:
    """SVC-004: READY=1 reaches $NOTIFY_SOCKET; without it nothing is sent.

    Args:
        tmp_path: Directory for the socket.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
        listener.bind(str(path))
        assert notify("READY=1\nSTATUS=ok", {"NOTIFY_SOCKET": str(path)})
        assert listener.recv(64) == b"READY=1\nSTATUS=ok"
    assert not notify("READY=1", {})
    assert not notify("READY=1", {"NOTIFY_SOCKET": "relative"})
    assert not notify("READY=1", {"NOTIFY_SOCKET": str(tmp_path / "gone")})
    assert not notify("READY=1", {"NOTIFY_SOCKET": "@vakr-test-no-listener"})


def test_signals_reach_the_service_callbacks() -> None:
    """CONF-003, 16.4: SIGHUP reloads and SIGTERM stops while the handlers are installed.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    seen: list[str] = []

    async def scenario() -> None:
        """Send both signals to this process.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with service_signals(lambda: seen.append("stop"), lambda: seen.append("reload")):
            os.kill(os.getpid(), signal.SIGHUP)
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(50):
                if len(seen) == 2:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert seen == ["reload", "stop"]


def test_log_lines_carry_priority_event_and_no_secret() -> None:
    """SVC-006, SEC-003: ``<4>[event] logger: message``; a known secret is redacted.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    secret = Secret(f"fake-{os.urandom(6).hex()}")
    stream = io.StringIO()
    handler = configure_service_logging(lambda: (secret,), stream=stream)
    try:
        logging.getLogger("vakr.test").warning(
            "oops %s\nsecond line", secret.reveal(), extra={"event_id": "test.event"}
        )
        try:
            int("not a number")
        except ValueError:
            logging.getLogger("vakr.test").exception("failed")
    finally:
        logging.getLogger("vakr").removeHandler(handler)
    text = stream.getvalue()
    assert text.startswith("<4>[test.event] vakr.test: oops ")
    assert secret.reveal() not in text
    assert " | second line" in text
    assert "<3>[log] vakr.test: failed" in text  # the traceback never reaches the line
    record = logging.LogRecord("vakr.x", 15, "", 0, "m", None, None)
    assert JournalFormatter().format(record).startswith("<6>[log]")


def test_entry_runs_the_service_with_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry point builds, warns, and returns the service's exit status.

    Args:
        tmp_path: Test directory.
        monkeypatch: Replaces the service with a quick stand-in.

    Returns:
        None

    Raises:
        None
    """
    paths = make_root(tmp_path, 161)

    class QuickService:
        """Stands in for ``CollectorService``: stops at once."""

        def __init__(self, parts: object, timings: object) -> None:
            """Keep the parts so they can be closed.

            Args:
                parts: The built parts.
                timings: Ignored.

            Returns:
                None

            Raises:
                None
            """
            del timings
            self.parts = parts

        def request_stop(self) -> None:
            """Ignore.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """

        def request_reload(self) -> None:
            """Ignore.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """

        async def run(self) -> int:
            """Close what the preflight opened and report success.

            Args:
                None

            Returns:
                int: 0.

            Raises:
                None
            """
            await self.parts.spool.close()  # ty: ignore[unresolved-attribute]
            await self.parts.close()  # ty: ignore[unresolved-attribute]
            return 0

    monkeypatch.setattr(entry, "CollectorService", QuickService)
    assert asyncio.run(entry.run_collector(paths)) == 0
