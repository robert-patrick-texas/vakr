# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the bounded fetcher: HTTPS limits, the gh fallback, and local commands.

Network failures must turn into ``FetchError`` (and so into "unknown"
rows) instead of crashing the report, responses are size-capped, plain
HTTP is refused, and GitHub calls fall back from ``gh api`` to
unauthenticated HTTPS (TOOLS-001). ``urlopen`` and ``gh`` are replaced, so
nothing leaves the machine.

Dependencies:
    pytest: ``monkeypatch`` and ``tmp_path``.
    updates_fetch: Module under test.

Safety:
    No network. Subprocess tests run only the current Python interpreter
    or a non-executable file in ``tmp_path``.

Example:
    $ uv run pytest tools/check-updates/test_updates_fetch.py
"""

import io
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import updates_fetch
from updates_fetch import CommandResult, Fetcher, FetchError


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        (b'{"ok": true}', ""),
        (b"<html>", "not valid JSON"),
        (b"x" * 64, "larger than 32 bytes"),
        (urllib.error.HTTPError("u", 503, "Service Unavailable", Message(), None), "HTTP 503"),
        (urllib.error.URLError("Name or service not known"), "Name or service not known"),
        (TimeoutError("timed out"), "timed out"),
    ],
)
def test_get_json_limits_and_errors(
    monkeypatch: pytest.MonkeyPatch, outcome: bytes | Exception, message: str
) -> None:
    """Bodies are size-capped and every failure becomes a FetchError (TOOLS-001).

    Args:
        monkeypatch: Replaces ``urlopen``.
        outcome: Body to return or exception to raise.
        message: Expected error text; empty for success.

    Returns:
        None

    Raises:
        AssertionError: If a failure escapes or is misreported.
    """

    def fake_urlopen(_request: Any, timeout: float) -> io.BytesIO:
        """Return or raise the scripted outcome.

        Args:
            _request: The request, ignored.
            timeout: Must be the fetcher's timeout.

        Returns:
            io.BytesIO: The scripted body; like a response, it is a context
            manager with ``read``.

        Raises:
            Exception: The scripted exception.
        """
        assert timeout == 5
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)

    monkeypatch.setattr(updates_fetch.urllib.request, "urlopen", fake_urlopen)
    fetcher = Fetcher(timeout=5, max_bytes=32)
    if not message:
        assert fetcher.get_json("https://example.org/x") == {"ok": True}
        return
    with pytest.raises(FetchError, match=message):
        fetcher.get_json("https://example.org/x")


def test_get_json_refuses_plain_http() -> None:
    """Only HTTPS is fetched (TOOLS-001; no downgrade to cleartext).

    Args:
        None

    Returns:
        None

    Raises:
        AssertionError: If HTTP is accepted.
    """
    with pytest.raises(FetchError, match="non-HTTPS"):
        Fetcher().get_json("http://example.org/")


def test_github_json_prefers_gh_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gh api`` answers when it works; otherwise unauthenticated HTTPS is used (TOOLS-001).

    Args:
        monkeypatch: Replaces ``shutil.which``, ``run``, and ``get_json``.

    Returns:
        None

    Raises:
        AssertionError: If the wrong transport is used.
    """
    results = iter(
        [
            CommandResult(0, '[{"tag_name": "v1"}]', ""),
            CommandResult(1, "", "auth required"),
            CommandResult(0, "not json", ""),
        ]
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], _cwd: Any = None) -> CommandResult:
        """Record the gh call and return the next scripted result.

        Args:
            argv: Command.
            _cwd: Ignored.

        Returns:
            CommandResult: Scripted result.

        Raises:
            None
        """
        calls.append(argv)
        return next(results)

    fetcher = Fetcher()
    monkeypatch.setattr(updates_fetch.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(fetcher, "run", fake_run)
    monkeypatch.setattr(fetcher, "get_json", lambda url: {"via": url})
    assert fetcher.github_json("repos/o/r/releases") == [{"tag_name": "v1"}]
    assert calls[0][:2] == ["gh", "api"]
    assert calls[0][-1] == "repos/o/r/releases"
    https = {"via": "https://api.github.com/repos/o/r/releases"}
    assert fetcher.github_json("repos/o/r/releases") == https  # gh not logged in
    assert fetcher.github_json("repos/o/r/releases") == https  # gh printed garbage


def test_run_captures_output_and_reports_missing_or_slow_programs(tmp_path: Path) -> None:
    """Commands run without a shell; missing, unrunnable, or hung programs raise FetchError.

    Args:
        tmp_path: Directory for a non-executable file.

    Returns:
        None

    Raises:
        AssertionError: If output or errors differ (TOOLS-001).
    """
    fetcher = Fetcher(timeout=5)
    done = fetcher.run([sys.executable, "-c", "import sys; print('1.2.3'); sys.exit(3)"])
    assert (done.returncode, done.stdout.strip()) == (3, "1.2.3")
    with pytest.raises(FetchError, match="is not installed"):
        fetcher.run(["check-updates-no-such-program"])
    script = tmp_path / "not-executable"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    with pytest.raises(FetchError, match="not-executable"):
        fetcher.run([str(script)])
    with pytest.raises(FetchError, match="timed out"):
        Fetcher(timeout=0.2).run([sys.executable, "-c", "import time; time.sleep(5)"])
