# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Bounded access to release APIs and local commands for check-updates.

Every fact check-updates reports comes either from an HTTPS JSON API
(python.org, go.dev, PyPI, npm, GitHub) or from a local command (``uv
--version``, ``go version``, ``uv lock --upgrade --dry-run``). This module
is the only place that touches the network or runs processes, so tests can
replace one ``Fetcher`` with recorded responses and never go online.

GitHub requests go through ``gh api`` when the GitHub CLI is installed and
logged in, which raises the API rate limit from 60 to 5,000 requests an
hour without check-updates ever holding the token. Otherwise they fall back
to unauthenticated HTTPS.

Dependencies:
    urllib.request: HTTPS GET with the system trust store; honours the
        standard ``HTTPS_PROXY`` and ``NO_PROXY`` environment variables.
    subprocess, shutil: Run ``gh`` and configured version commands with
        argument lists, never through a shell.

Safety:
    Only ``https://`` URLs are fetched and certificate verification is
    never disabled. Each request and command has a timeout and each
    response a size cap, so a slow or hostile endpoint cannot hang or
    exhaust the tool. Commands come from the project's own config file.
    No credential is read, stored, or printed: ``gh`` keeps its own token.

Example:
    >>> fetcher = Fetcher(timeout=10)
    >>> fetcher.get_json("http://example.com/")
    Traceback (most recent call last):
    ...
    updates_fetch.FetchError: refusing non-HTTPS URL http://example.com/
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 20.0
# PyPI's JSON for large projects (ruff, uv) is several megabytes because it
# lists every file of every release; the cap leaves room for that growth.
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
USER_AGENT = "check-updates (portable toolchain and dependency update checker)"


class FetchError(Exception):
    """A request or command failed; the affected item is reported as unknown."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of one local command.

    Attributes:
        returncode: Process exit status.
        stdout: Captured standard output, decoded as UTF-8.
        stderr: Captured standard error, decoded as UTF-8.
    """

    returncode: int
    stdout: str
    stderr: str


class Fetcher:
    """Performs HTTPS GETs, GitHub API calls, and local commands with limits.

    Instances hold only settings, so one fetcher is safe to share between
    the worker threads that run checks concurrently.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
        use_gh: bool = True,
    ) -> None:
        """Store the limits applied to every request and command.

        Args:
            timeout: Seconds allowed for one request or command.
            max_bytes: Largest response body accepted, in bytes.
            use_gh: Try ``gh api`` before unauthenticated HTTPS for GitHub.

        Returns:
            None

        Raises:
            None
        """
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.use_gh = use_gh

    def get_json(self, url: str, accept: str = "") -> Any:
        """GET an HTTPS URL and decode its JSON body.

        Args:
            url: Absolute ``https://`` URL.
            accept: ``Accept`` header value, for registries that serve a
                smaller document on request; empty sends none.

        Returns:
            Any: The decoded JSON document.

        Raises:
            FetchError: For a non-HTTPS URL, a network or HTTP error, a
                timeout, an oversized body, or invalid JSON.
        """
        if not url.startswith("https://"):
            raise FetchError(f"refusing non-HTTPS URL {url}")
        # The scheme is checked above, so S310's file:// concern cannot apply.
        headers = {"User-Agent": USER_AGENT} | ({"Accept": accept} if accept else {})
        request = urllib.request.Request(url, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                body = response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise FetchError(f"{url}: HTTP {exc.code} {exc.reason}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise FetchError(f"{url}: {reason}") from None
        if len(body) > self.max_bytes:
            raise FetchError(f"{url}: response larger than {self.max_bytes} bytes")
        try:
            return json.loads(body)
        except ValueError:
            raise FetchError(f"{url}: response is not valid JSON") from None

    def github_json(self, path: str) -> Any:
        """Call the GitHub REST API, preferring the authenticated ``gh`` CLI.

        Args:
            path: API path without a leading slash, such as
                ``repos/OWNER/REPO/releases?per_page=10``.

        Returns:
            Any: The decoded JSON document.

        Raises:
            FetchError: If both ``gh api`` and HTTPS fail.
        """
        if self.use_gh and shutil.which("gh"):
            try:
                accept = "Accept: application/vnd.github+json"
                result = self.run(["gh", "api", "-H", accept, path])
                if result.returncode == 0:
                    return json.loads(result.stdout)
            except FetchError, ValueError:
                pass  # gh missing auth or odd output: unauthenticated HTTPS may still work
        return self.get_json(f"https://api.github.com/{path}")

    def run(self, argv: Sequence[str], cwd: Path | None = None) -> CommandResult:
        """Run a command with an argument list and capture its output.

        ``VIRTUAL_ENV`` is removed from the child's environment so ``uv``
        resolves the project's interpreter rather than the throwaway
        environment ``uv run`` created for this script.

        Args:
            argv: Program and arguments; never passed through a shell.
            cwd: Working directory, normally the repository root.

        Returns:
            CommandResult: Exit status and decoded output, even on failure.

        Raises:
            FetchError: If the program is not installed or times out.
        """
        env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
        try:
            # Commands come from the project's own config, never from fetched data.
            done = subprocess.run(  # noqa: S603
                list(argv),
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError:
            raise FetchError(f"{argv[0]} is not installed") from None
        except subprocess.TimeoutExpired:
            raise FetchError(f"{argv[0]} timed out after {self.timeout:g} s") from None
        except OSError as exc:
            raise FetchError(f"{argv[0]}: {exc.strerror}") from None
        return CommandResult(done.returncode, done.stdout, done.stderr)
