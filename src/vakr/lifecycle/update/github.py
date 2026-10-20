"""GitHub REST API access for release discovery.

Self-update lists recent releases of a public repository without a token.
Unauthenticated API calls are rate limited per source address, which on an
enterprise network is shared by many hosts behind one NAT, so a rate-limit
reply is reported as such, with the reset time, rather than as a generic
failure.

Dependencies:
    vakr.lifecycle.egress.client: ``Response`` type returned by the client.
    vakr.lifecycle.update.releases: Release parsing.

Safety:
    The repository name is validated before it is put into a URL, and only
    HTTPS asset URLs are kept. Nothing fetched is trusted until ``verify``
    has checked the signature and checksum.

Example:
    >>> fetch_releases(client, "robert-patrick-texas/vakr")[0].tag  # doctest: +SKIP
    'v0.2.0'
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from vakr.lifecycle.egress.client import Response
from vakr.lifecycle.update.releases import RateLimitError, Release, ReleaseError, parse_release
from vakr.lifecycle.update.versions import Version

GITHUB_API = "https://api.github.com"
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class HttpGetter(Protocol):
    """The subset of ``HttpsClient`` the updater needs; tests inject fakes."""

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> Response:
        """Fetch ``url`` into memory.

        Args:
            url: HTTPS URL.
            headers: Extra request headers.

        Returns:
            Response: Final response after redirects.

        Raises:
            EgressError: On transport failure.
        """
        ...

    def download(
        self, url: str, dest: Path, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> int:
        """Stream ``url`` to ``dest``.

        Args:
            url: HTTPS URL.
            dest: Destination file.
            max_bytes: Size limit.
            headers: Extra request headers.

        Returns:
            int: Bytes written.

        Raises:
            EgressError: On transport failure or non-200 status.
        """
        ...


def fetch_releases(http: HttpGetter, repo: str, api_base: str = GITHUB_API) -> list[Release]:
    """List the 30 most recent releases of ``repo`` with parseable tags.

    Args:
        http: HTTPS client.
        repo: ``owner/name``.
        api_base: API root; overridden by tests or GitHub Enterprise.

    Returns:
        list[Release]: Releases in API order; tags that are not release
        versions are skipped.

    Raises:
        ReleaseError: For an invalid repository name or an API error.
        RateLimitError: When the API rate limit is exhausted.
        EgressError: When the API cannot be reached.
    """
    response = http.get(f"{api_base}/repos/{_repo(repo)}/releases?per_page=30", API_HEADERS)
    data = _checked_json(response)
    if not isinstance(data, list):
        raise ReleaseError("unexpected releases response (not a list)")
    return [r for item in data if (r := parse_release(item)) is not None]


def fetch_release_by_tag(
    http: HttpGetter, repo: str, tag: str, api_base: str = GITHUB_API
) -> Release:
    """Fetch the release named by ``tag``, for example to re-download for rollback.

    Args:
        http: HTTPS client.
        repo: ``owner/name``.
        tag: Release tag such as ``v0.2.0``.
        api_base: API root.

    Returns:
        Release: The release.

    Raises:
        ReleaseError: When the release does not exist or the API fails.
        RateLimitError: When the API rate limit is exhausted.
        EgressError: When the API cannot be reached.
    """
    url = f"{api_base}/repos/{_repo(repo)}/releases/tags/{Version.parse(tag).tag()}"
    release = parse_release(_checked_json(http.get(url, API_HEADERS)))
    if release is None:
        raise ReleaseError(f"release {tag} has an unparseable tag")
    return release


def _repo(repo: str) -> str:
    """Validate an ``owner/name`` repository string before URL use.

    Args:
        repo: Candidate repository.

    Returns:
        str: The same string.

    Raises:
        ReleaseError: When it is not ``owner/name``.
    """
    if not REPO_PATTERN.fullmatch(repo):
        raise ReleaseError(f"invalid repository {repo!r}; expected owner/name")
    return repo


def _checked_json(response: Response) -> Any:
    """Return the JSON body of a successful API response.

    Args:
        response: API response.

    Returns:
        Any: Decoded JSON.

    Raises:
        RateLimitError: For 403/429 with the quota exhausted.
        ReleaseError: For any other non-200 status.
        EgressError: When the body is not JSON.
    """
    if response.status in {403, 429} and response.headers.get("x-ratelimit-remaining") == "0":
        reset = response.headers.get("x-ratelimit-reset", "")
        when = (
            datetime.fromtimestamp(int(reset), UTC).strftime("%H:%M UTC")
            if reset.isdigit()
            else "later"
        )
        raise RateLimitError(
            f"GitHub API rate limit exhausted for this address; it resets at {when}. "
            "Hosts behind one NAT share the limit; retry later or use --file."
        )
    if response.status == 404:
        raise ReleaseError(f"not found: {response.url}")
    if response.status != 200:
        raise ReleaseError(f"GitHub API returned HTTP {response.status} for {response.url}")
    return response.json()
