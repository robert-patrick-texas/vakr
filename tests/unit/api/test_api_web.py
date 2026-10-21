"""The web UI served same-origin outside ``/api/`` (ADR-035 #3, #4, #13; WEB-T10, UI-012, 15.4).

``web_mount`` attaches the static build to the real application: cache
headers per kind of file, the single-page-app fallback, the JSON envelope
kept for unknown API paths, the page CSP with the bootstrap's hash and no
``unsafe-inline``/``unsafe-eval``, precompressed variants, ETag
revalidation, and no path that reaches outside the build.

Dependencies:
    fastapi.testclient; tests.unit.api.api_testkit, web_testkit.

Example:
    $ uv run pytest tests/unit/api/test_api_web.py
"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.api.api_testkit import ApiKit
from tests.unit.api.web_testkit import (
    BOOTSTRAP,
    INDEX,
    MODULE,
    MODULE_PATH,
    fake_build,
    script_hash,
)
from vakr.api.app import create_app
from vakr.api.middleware_parts import SECURITY_HEADERS
from vakr.api.web import IMMUTABLE, accepted, mount_web
from vakr.api.web_files import load_web_build, web_directory
from vakr.paths import InstallPaths


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Return a client of the real app with a fake build attached.

    Args:
        tmp_path: Test directory.

    Returns:
        TestClient: The client.

    Raises:
        None
    """
    web = mount_web(fake_build(tmp_path))
    assert web is not None
    return TestClient(create_app(ApiKit.create().context, web=web), client=("127.0.0.1", 1))


def test_web_t10_immutable_assets_cache_for_a_year_and_pages_revalidate(client: TestClient) -> None:
    """ADR-035 #4, WEB-T10: hashed files are immutable; HTML is no-cache with the page CSP.

    Args:
        client: The app with the build.

    Returns:
        None

    Raises:
        None
    """
    module = client.get("/" + MODULE_PATH, headers={"Accept-Encoding": "identity"})
    assert module.status_code == 200
    assert module.headers["cache-control"] == IMMUTABLE == "public, max-age=31536000, immutable"
    assert module.headers["content-type"].startswith("text/javascript")
    assert module.content == MODULE
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-cache"
    assert page.headers["content-type"] == "text/html; charset=utf-8"
    csp = page.headers["content-security-policy"]
    assert f"script-src 'self' '{script_hash(BOOTSTRAP)}';" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    for directive in ("frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'",
                      "connect-src 'self'", "style-src 'self'", "object-src 'none'"):  # fmt: skip
        assert directive in csp
    for name, value in SECURITY_HEADERS:
        if name != b"content-security-policy":
            assert page.headers[name.decode()] == value.decode()
    assert client.get("/_app/version.json").headers["cache-control"] == "no-cache"


def test_adr035_client_routes_get_the_fallback_and_api_paths_keep_the_envelope(
    client: TestClient,
) -> None:
    """ADR-035 #3: deep links answer index.html; unknown API and asset paths are 404 envelopes.

    Args:
        client: The app with the build.

    Returns:
        None

    Raises:
        None
    """
    for path in ("/devices/0190a3c4-0000-7000-8000-00000000002a", "/login", "/a/b/c?x=1"):
        answer = client.get(path)
        assert answer.status_code == 200
        assert answer.text == INDEX
    for method, path in (("GET", "/api/v1/nowhere"), ("POST", "/api/v1/nowhere"), ("GET", "/api")):
        answer = client.request(method, path)
        assert answer.status_code == 404
        assert answer.json()["code"] == "not_found"
        if path.startswith("/api/"):
            assert answer.headers["cache-control"] == "no-store"
    missing = client.get("/_app/immutable/chunks/missing.js")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"
    assert client.post("/devices").status_code == 405  # pages never change state (15.4)
    assert client.get("/api/v1/health/live").json()["status"] == "ok"


def test_web_t10_path_traversal_cannot_leave_the_build(tmp_path: Path, client: TestClient) -> None:
    """15.4: requests are looked up in the in-memory index; nothing outside the build is served.

    Args:
        tmp_path: Test directory (holds a file beside the build).
        client: The app with the build.

    Returns:
        None

    Raises:
        None
    """
    secret = tmp_path / "outside.txt"
    secret.write_text("not part of the web build", encoding="utf-8")
    probes = [
        "/../outside.txt",
        "/%2e%2e/outside.txt",
        "/_app/../../outside.txt",
        "/%2e%2e%2foutside.txt",
        "/..%5coutside.txt",
        "/vakr-web.json/../../outside.txt",
    ]
    for probe in probes:
        answer = client.get(probe)
        assert b"not part of the web build" not in answer.content, probe
        assert answer.status_code in (200, 404), probe


def test_web_build_skips_symbolic_links(tmp_path: Path) -> None:
    """15.4: a link inside the build to a file outside it is never indexed or served.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    build = fake_build(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("outside", encoding="utf-8")
    (build / "link.txt").symlink_to(secret)
    assert "link.txt" not in load_web_build(build).files


def test_precompressed_variants_follow_accept_encoding(client: TestClient) -> None:
    """ADR-035 #4: br, then gzip, when accepted; q=0 refuses a coding; Vary names the header.

    Args:
        client: The app with the build.

    Returns:
        None

    Raises:
        None
    """
    path = "/" + MODULE_PATH
    br = client.get(path, headers={"Accept-Encoding": "gzip, br"})
    assert br.headers["content-encoding"] == "br"
    assert br.headers["vary"] == "Accept-Encoding"
    gz = client.get(path, headers={"Accept-Encoding": "br;q=0, gzip"})
    assert gz.headers["content-encoding"] == "gzip"
    assert gz.content == MODULE
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert accepted("*") == ("br", "gzip")
    assert accepted("deflate") == ()


def test_etag_revalidation_answers_304(client: TestClient) -> None:
    """12.9: a no-cache page revalidates cheaply with its ETag.

    Args:
        client: The app with the build.

    Returns:
        None

    Raises:
        None
    """
    first = client.get("/", headers={"Accept-Encoding": "identity"})
    again = client.get(
        "/", headers={"Accept-Encoding": "identity", "If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "manifest",
    [
        {"format": 2, "script_hashes": []},
        {"format": 1, "script_hashes": ["'unsafe-inline'"]},
        {"format": 1, "script_hashes": "sha256-x"},
        ["not", "an", "object"],
    ],
)
def test_a_bad_manifest_means_no_pages(tmp_path: Path, manifest: object) -> None:
    """ADR-035 #13: only format-1 lists of SHA-256 sources are accepted into the CSP.

    Args:
        tmp_path: Test directory.
        manifest: The broken manifest.

    Returns:
        None

    Raises:
        None
    """
    assert mount_web(fake_build(tmp_path, manifest=manifest)) is None


def test_missing_build_is_logged_once_and_the_api_still_serves(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """E.31: without a build, one warning names the directory; the API answers, pages are 404.

    Args:
        tmp_path: Test directory.
        caplog: Captures the log.

    Returns:
        None

    Raises:
        None
    """
    with caplog.at_level(logging.WARNING, logger="vakr.api.web"):
        web = mount_web(tmp_path / "absent")
    events = [r for r in caplog.records if getattr(r, "event_id", "") == "api.web_unavailable"]
    assert web is None
    assert len(events) == 1
    assert str(tmp_path / "absent") in events[0].getMessage()
    client = TestClient(create_app(ApiKit.create().context, web=web), client=("127.0.0.1", 1))
    assert client.get("/api/v1/health/live").status_code == 200
    assert client.get("/").json()["code"] == "not_found"


def test_e31_build_location_is_vakr_web_dir_then_the_package(tmp_path: Path) -> None:
    """E.31: ``VAKR_WEB_DIR`` for development and tests, else ``/opt/vakr/share/web``.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path)
    assert web_directory(paths, {}) == tmp_path / "opt/vakr/share/web"
    assert web_directory(paths, {"VAKR_WEB_DIR": "/srv/web"}) == Path("/srv/web")
    assert web_directory(InstallPaths.under(Path("/")), {}) == Path("/opt/vakr/share/web")
