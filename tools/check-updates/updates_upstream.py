# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Fetch latest upstream releases once per item and share them between checks.

Several rows depend on the same upstream release: the ``.python-version``
pin, the ``requires-python`` floor, and the interpreter uv selects all
compare with the latest CPython; the ``.node-version`` pin, the
``engines.node`` floor, and the installed Node all read one nodejs.org
index; the installed nfpm and a pinned nfpm compare with the same GitHub
release. ``Upstream`` fetches each source at
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
from typing import Any, cast
from urllib.parse import quote

from updates_fetch import Fetcher, FetchError
from updates_sources import (
    SOURCES,
    Latest,
    github_latest,
    go_latest,
    npm_latest,
    npm_line_latest,
    pypi_latest,
    python_latest,
)

PYTHON_RELEASES_URL = (
    "https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false"
)
GO_RELEASES_URL = "https://go.dev/dl/?mode=json"
NODE_RELEASES_URL = "https://nodejs.org/dist/index.json"
NPM_ABBREVIATED = "application/vnd.npm.install-v1+json"
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
        node_url: str = NODE_RELEASES_URL,
    ) -> None:
        """Bind the fetcher and the configurable release-list URLs.

        Args:
            fetcher: Network access, real or fake.
            python_url: python.org release API URL.
            go_url: go.dev download list URL.
            node_url: nodejs.org release index URL.

        Returns:
            None

        Raises:
            None
        """
        self.fetcher = fetcher
        self.python_url = python_url
        self.go_url = go_url
        self.node_url = node_url
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._cache: dict[str, object] = {}

    def _memo[T](self, key: str, compute: Callable[[], T]) -> T:
        """Return the cached result for ``key``, computing it at most once.

        Args:
            key: Cache key such as ``"pypi:ruff"``.
            compute: Fetches and parses the release (or document) when not cached.

        Returns:
            T: The cached or freshly computed value.

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
        return cast(T, found)

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

    def node(self, parse: Callable[[Any], Latest], key: str) -> Latest:
        """Return a Node release chosen by ``parse`` from the nodejs.org index.

        The index is fetched once per run however many rows read it.

        Args:
            parse: Picks one release from the decoded index (LTS or Current).
            key: Cache key for that choice, such as ``"node:lts"``.

        Returns:
            Latest: Release and notes URL.

        Raises:
            FetchError: If nodejs.org cannot be reached or parsed.
        """
        index = self._memo("node:index", lambda: self.fetcher.get_json(self.node_url))
        return self._memo(key, lambda: parse(index))

    def npm_line(self, name: str, major: int) -> Latest:
        """Return the newest release of ``name`` on one major line.

        Args:
            name: npm package name, scoped names included.
            major: Major version to stay on.

        Returns:
            Latest: Release and its npm page.

        Raises:
            FetchError: If the registry cannot be reached or lists no such release.
        """
        url = f"https://registry.npmjs.org/{quote(name, safe='@')}"

        def parse() -> Latest:
            """Fetch the package's version list and pick the newest on the line.

            Args:
                None

            Returns:
                Latest: Release and its npm page.

            Raises:
                FetchError: If the fetch fails or the line has no stable release.
            """
            # The abbreviated packument lists every version in about a fifth of the bytes.
            document = self.fetcher.get_json(url, accept=NPM_ABBREVIATED)
            return npm_line_latest(document, name, major)

        return self._memo(f"npm:{name}@{major}", parse)

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
            # A scoped name's slash is encoded, as the registry documents: @scope%2fname.
            url = f"https://registry.npmjs.org/{quote(name, safe='@')}/latest"
            return self._memo(f"npm:{name}", lambda: npm_latest(self.fetcher.get_json(url), name))
        raise ValueError(f"unsupported source {source!r}; use one of {', '.join(SOURCES)}")
