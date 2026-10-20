"""Version forms (VER-002) and release discovery and selection (UPD-001, UPD-008).

Dependencies:
    tests.unit.lifecycle.helpers: API object builders.

Example:
    $ uv run pytest tests/unit/lifecycle/test_update_versions.py
"""

import pytest

from tests.unit.lifecycle.helpers import FakeHttp, as_json, release_json
from vakr.lifecycle.egress.client import Response
from vakr.lifecycle.update.github import fetch_release_by_tag, fetch_releases
from vakr.lifecycle.update.releases import (
    RateLimitError,
    Release,
    ReleaseError,
    asset_names,
    newest,
    notes_excerpt,
    parse_release,
)
from vakr.lifecycle.update.versions import Version


@pytest.mark.parametrize("text", ["0.2.0-rc.1", "v0.2.0-rc.1", "0.2.0rc1", "0.2.0~rc.1"])
def test_all_rc_spellings_parse_to_one_version(text: str) -> None:
    """VER-002: semver, tag, PEP 440, and Debian forms are the same release.

    Args:
        text: One spelling.

    Returns:
        None

    Raises:
        None
    """
    version = Version.parse(text)
    assert version == Version(0, 2, 0, 1)
    assert (version.semver(), version.pep440(), version.debian(), version.tag()) == (
        "0.2.0-rc.1",
        "0.2.0rc1",
        "0.2.0~rc.1",
        "v0.2.0-rc.1",
    )
    assert version.is_prerelease


def test_ordering_puts_candidates_before_final() -> None:
    """VER-002: rc.N < rc.N+1 < final < next patch; local labels are ignored.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    ordered = ["0.0.0+unknown", "0.2.0-rc.1", "0.2.0rc2", "0.2.0", "0.2.1~rc.1", "0.10.0"]
    parsed = [Version.parse(v) for v in ordered]
    assert parsed == sorted(parsed)
    assert str(Version.parse("1.2.3")) == "1.2.3"
    assert not Version.parse("1.2.3").is_prerelease
    assert (Version(1, 0, 0) == "1.0.0") is False
    with pytest.raises(TypeError):
        _ = Version(1, 0, 0) < "1.0.0"  # type: ignore[operator]


@pytest.mark.parametrize("bad", ["", "1.2", "01.2.3", "1.2.3-beta.1", "1.2.3-rc1", "v", "x1.2.3"])
def test_invalid_versions_are_rejected(bad: str) -> None:
    """Anything outside the documented forms is refused, not guessed.

    Args:
        bad: Invalid text.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match="not a release version"):
        Version.parse(bad)


def _release(tag: str, *, pre: bool = False, draft: bool = False) -> Release:
    """Build a release record for selection tests.

    Args:
        tag: Tag name.
        pre: GitHub pre-release flag.
        draft: Draft flag.

    Returns:
        Release: The record.

    Raises:
        None
    """
    return Release(Version.parse(tag), tag, prerelease=pre, draft=draft)


def test_channel_selection_and_hold_ceiling() -> None:
    """Prerelease takes the newest non-draft; stable skips candidates; hold caps (UPD-008).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    releases = [
        _release("v0.4.0", draft=True),
        _release("v0.3.0-rc.1", pre=True),
        _release("v0.2.1"),
        _release("v0.2.2", pre=True),
    ]
    picked = newest(releases, "prerelease")
    assert picked is not None
    assert picked.tag == "v0.3.0-rc.1"
    stable = newest(releases, "stable")
    assert stable is not None
    assert stable.tag == "v0.2.1"
    held = newest(releases, "prerelease", Version.parse("0.2.2"))
    assert held is not None
    assert held.tag == "v0.2.2"
    assert newest(releases, "stable", Version.parse("0.2.0")) is None
    with pytest.raises(ReleaseError, match="unknown update channel"):
        newest(releases, "nightly")


def test_parse_release_keeps_https_assets_only() -> None:
    """API objects become records; non-HTTPS asset URLs and bad tags are dropped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    item = release_json("v0.3.0", {"a.deb": "https://x/a.deb", "b": "http://x/b"}, body="notes")
    release = parse_release(item)
    assert release is not None
    assert dict(release.assets) == {"a.deb": "https://x/a.deb"}
    assert release.body == "notes"
    assert parse_release(release_json("nightly", {})) is None
    assert parse_release("not an object") is None


def test_asset_lookup_tolerates_github_renaming() -> None:
    """A ``~`` in a Debian pre-release name may be uploaded as ``.``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    names = asset_names("vakr", Version.parse("0.3.0-rc.2"), "amd64")
    assert names.package == "vakr_0.3.0~rc.2_amd64.deb"
    release = Release(Version(0, 3, 0, 2), "v0.3.0-rc.2", assets={"vakr_0.3.0.rc.2_amd64.deb": "u"})
    assert release.asset_url(names.package) == "u"
    assert release.asset_url("missing") is None


def test_notes_excerpt_truncates() -> None:
    """The summary shows a bounded excerpt of the release notes.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    body = "\n".join(f"line {n}" for n in range(30))
    excerpt = notes_excerpt(body, max_lines=3)
    assert excerpt == "line 0\nline 1\nline 2\n..."
    assert notes_excerpt("short\n\n") == "short"


def _stub(responses: dict[str, Response]) -> FakeHttp:
    """Build a fake client answering ``get`` from ``responses``.

    Args:
        responses: URL to response.

    Returns:
        FakeHttp: The fake.

    Raises:
        None
    """
    fake = FakeHttp()
    fake.responses.update(responses)
    return fake


API = "https://api.example"
LIST_URL = f"{API}/repos/o/r/releases?per_page=30"


def test_fetch_releases_parses_and_skips_unknown_tags() -> None:
    """UPD-001: discovery lists recent releases and ignores non-version tags.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    body = as_json([release_json("v0.3.0", {}), release_json("latest", {})])
    stub = _stub({LIST_URL: Response(LIST_URL, 200, {}, body)})
    releases = fetch_releases(stub, "o/r", API)
    assert [r.tag for r in releases] == ["v0.3.0"]
    tag_url = f"{API}/repos/o/r/releases/tags/v0.3.0"
    stub.responses[tag_url] = Response(tag_url, 200, {}, as_json(release_json("v0.3.0", {})))
    assert fetch_release_by_tag(stub, "o/r", "0.3.0", API).tag == "v0.3.0"


def test_rate_limit_is_reported_clearly() -> None:
    """A 403 with an exhausted quota names the rate limit and its reset time.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"}
    stub = _stub({LIST_URL: Response(LIST_URL, 403, headers, b"{}")})
    with pytest.raises(RateLimitError, match=r"rate limit exhausted.*resets at \d\d:\d\d UTC"):
        fetch_releases(stub, "o/r", API)
    stub.responses[LIST_URL] = Response(LIST_URL, 429, {"x-ratelimit-remaining": "0"}, b"")
    with pytest.raises(RateLimitError, match="later"):
        fetch_releases(stub, "o/r", API)


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (500, b"", "HTTP 500"),
        (404, b"", "not found"),
        (200, b"{}", "not a list"),
        (403, b"", "403"),
    ],
)
def test_api_errors(status: int, body: bytes, message: str) -> None:
    """Other API failures raise ``ReleaseError`` with the status.

    Args:
        status: HTTP status.
        body: Response body.
        message: Expected text.

    Returns:
        None

    Raises:
        None
    """
    stub = _stub({LIST_URL: Response(LIST_URL, status, {}, body)})
    with pytest.raises(ReleaseError, match=message):
        fetch_releases(stub, "o/r", API)


def test_repository_name_is_validated() -> None:
    """A repository string cannot inject URL components.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ReleaseError, match="invalid repository"):
        fetch_releases(_stub({}), "o/r/../../x?y", API)
    tag_url = f"{API}/repos/o/r/releases/tags/v0.3.0"
    stub = _stub({tag_url: Response(tag_url, 200, {}, as_json({"tag_name": "junk"}))})
    with pytest.raises(ReleaseError, match="unparseable"):
        fetch_release_by_tag(stub, "o/r", "v0.3.0", API)
