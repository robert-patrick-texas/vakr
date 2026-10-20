# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Fetch latest upstream releases once per item and share them between checks.

Several rows depend on the same upstream release: the ``.python-version``
pin, the ``requires-python`` floor, and the interpreter uv selects all
compare with the latest CPython; the installed nfpm and a pinned nfpm
compare with the same GitHub release. ``Upstream`` fetches each source at
most once per run, even when checks run concurrently, and caches failures
too, so an unreachable API costs one timeout rather than one per row.

Dependencies:
    updates_fetch: Network access, replaced by a fake in tests.
    updates_sources: Parsers for each release listing.
    threading: One lock per cache key, so concurrent checks share a fetch.

Safety:
    URLs are built from config values and fixed API hosts, with names
    percent-encoded. Nothing from a response is executed.

Example:
    >>> upstream = Upstream(Fetcher(timeout=10))
    >>> latest = upstream.lookup("pypi", "ruff")  # doctest: +SKIP
    >>> latest.version.text  # doctest: +SKIP
    '0.17.1'
"""

import threading
from collections.abc import Callable
from urllib.parse import quote

from updates_fetch import Fetcher, FetchError
from updates_sources import (
    SOURCES,
    Latest,
    github_latest,
    go_latest,
    npm_latest,
    pypi_latest,
    python_latest,
)

PYTHON_RELEASES_URL = (
    "https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false"
)
GO_RELEASES_URL = "https://go.dev/dl/?mode=json"
# Upstream JSON shapes change without notice; these mean "unexpected document".
_SHAPE_ERRORS = (KeyError, TypeError, AttributeError, ValueError)


class Upstream:
    """Fetches latest releases by source and caches results, failures included.

    Thread-safe: concurrent requests for one key wait for a single fetch.
    """

    def __init__(
        self,
        fetcher: Fetcher,
        python_url: str = PYTHON_RELEASES_URL,
        go_url: str = GO_RELEASES_URL,
    ) -> None:
        """Bind the fetcher and the configurable release-list URLs.

        Args:
            fetcher: Network access, real or fake.
            python_url: python.org release API URL.
            go_url: go.dev download list URL.

        Returns:
            None

        Raises:
            None
        """
        self.fetcher = fetcher
        self.python_url = python_url
        self.go_url = go_url
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._cache: dict[str, Latest | FetchError] = {}

    def _memo(self, key: str, compute: Callable[[], Latest]) -> Latest:
        """Return the cached result for ``key``, computing it at most once.

        Args:
            key: Cache key such as ``"pypi:ruff"``.
            compute: Fetches and parses the release when not cached.

        Returns:
            Latest: The cached or freshly computed release.

        Raises:
            FetchError: The cached or new failure, so a dead endpoint is
                contacted once and every dependent item reports it.
        """
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._cache:
                try:
                    self._cache[key] = compute()
                except FetchError as exc:
                    self._cache[key] = exc
                except _SHAPE_ERRORS as exc:
                    self._cache[key] = FetchError(f"{key}: unexpected response ({exc!r})")
            found = self._cache[key]
        if isinstance(found, FetchError):
            raise found
        return found

    def python(self) -> Latest:
        """Return the latest stable CPython release.

        Args:
            None

        Returns:
            Latest: Release and notes URL.

        Raises:
            FetchError: If python.org cannot be reached or parsed.
        """
        return self._memo("python", lambda: python_latest(self.fetcher.get_json(self.python_url)))

    def go(self) -> Latest:
        """Return the latest stable Go release.

        Args:
            None

        Returns:
            Latest: Release and notes URL.

        Raises:
            FetchError: If go.dev cannot be reached or parsed.
        """
        return self._memo("go", lambda: go_latest(self.fetcher.get_json(self.go_url)))

    def lookup(self, source: str, name: str, per_page: int = 10) -> Latest:
        """Return the latest release of ``name`` from ``source``.

        Args:
            source: ``"pypi"``, ``"github"``, or ``"npm"``.
            name: Package name, or ``OWNER/REPO`` for GitHub.
            per_page: GitHub releases fetched; lower it for repositories
                whose releases carry hundreds of assets.

        Returns:
            Latest: Release and notes URL.

        Raises:
            FetchError: If the source cannot be reached or parsed.
            ValueError: If ``source`` is not supported.
        """
        safe = quote(name, safe="/@")
        if source == "pypi":
            url = f"https://pypi.org/pypi/{safe}/json"
            return self._memo(f"pypi:{name}", lambda: pypi_latest(self.fetcher.get_json(url), name))
        if source == "github":
            path = f"repos/{safe}/releases?per_page={per_page}"
            return self._memo(
                f"github:{name}", lambda: github_latest(self.fetcher.github_json(path), name)
            )
        if source == "npm":
            url = f"https://registry.npmjs.org/{safe}/latest"
            return self._memo(f"npm:{name}", lambda: npm_latest(self.fetcher.get_json(url), name))
        raise ValueError(f"unsupported source {source!r}; use one of {', '.join(SOURCES)}")
