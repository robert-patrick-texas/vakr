"""The OpenAPI document of API v1, generated deterministically and checked in (API-008, API-T01).

``openapi_text`` renders the document of an app built without services,
with sorted keys and a final newline, so the same code always produces the
same bytes. ``make openapi`` (``vakr api openapi --output
docs/api/openapi.json``) writes it, and a test fails when the checked-in
file differs from what the code generates. ``info.version`` is the API
major version, never the release, so the file does not change with every
release (VER-001).

``describe_security`` adds what FastAPI cannot infer: the session cookie,
the kiosk bearer token, and the local socket, and marks the endpoints that
need no authentication.

Dependencies:
    json: Rendering.
    vakr.api.app: The application (imported on use).

Safety:
    The document holds route shapes only: no example contains a token,
    password, or database row ID (API-T05).

Example:
    >>> "/api/v1/health/live" in ANONYMOUS_PATHS
    True
"""

import json
from collections.abc import Iterable
from typing import Any

ANONYMOUS_PATHS = frozenset({
    "/api/v1/health/live",
    "/api/v1/health/ready",
    "/api/v1/auth/login",
    "/api/v1/auth/kiosk",
    "/api/v1/auth/kiosk/forget",
})  # fmt: skip
"""Endpoints any caller may use; every other one needs a role."""
SECURITY_SCHEMES = {
    "sessionCookie": {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-vakr_session",
        "description": "Set by POST /api/v1/auth/login over HTTPS (vakr_session over loopback "
        "HTTP). State-changing requests also need the X-CSRF-Token header.",
    },
    "kioskToken": {
        "type": "http",
        "scheme": "bearer",
        "description": "A kiosk token (vkk_...) created by an admin: operator role, read-only.",
    },
    "kioskCookie": {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-vakr_kiosk",
        "description": "Set by POST /api/v1/auth/kiosk over HTTPS (vakr_kiosk over loopback "
        "HTTP) for a kiosk display: operator role, read-only. A session cookie wins over it.",
    },
    "replicationToken": {
        "type": "http",
        "scheme": "bearer",
        "description": "A replication token (vkr_...) this node issued for its partner "
        "(sudo vakr replication token create): /api/v1/replication/* only (MPOS-006).",
    },
}
REPLICATION_PREFIX = "/api/v1/replication/"


def describe_security(
    document: dict[str, Any], prefix: str, anonymous: Iterable[str]
) -> dict[str, Any]:
    """Add the security schemes and mark anonymous endpoints.

    Args:
        document: The generated OpenAPI document.
        prefix: The API prefix, ``/api/v1``.
        anonymous: Paths that need no authentication.

    Returns:
        dict[str, Any]: The same document, extended.

    Raises:
        None
    """
    document.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    document["security"] = [{"sessionCookie": []}, {"kioskToken": []}, {"kioskCookie": []}]
    open_paths = set(anonymous)
    for path, operations in document.get("paths", {}).items():
        for operation in operations.values():
            # Handler docstrings document Python arguments; readers need the first paragraph.
            text = str(operation.get("description", ""))
            operation["description"] = text.split("\n\nArgs:", 1)[0].strip()
            if path in open_paths or not path.startswith(prefix):
                operation["security"] = []
            elif path.startswith(REPLICATION_PREFIX):
                operation["security"] = [{"replicationToken": []}]
    return document


def openapi_document() -> dict[str, Any]:
    """Generate the document from an app built without services.

    Args:
        None

    Returns:
        dict[str, Any]: The OpenAPI document.

    Raises:
        None
    """
    from vakr.api.app import create_app  # the app imports this module

    return create_app().openapi()


def openapi_text() -> str:
    """Render the document deterministically.

    Args:
        None

    Returns:
        str: JSON with two-space indentation, sorted keys, and a final newline.

    Raises:
        None
    """
    return json.dumps(openapi_document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
