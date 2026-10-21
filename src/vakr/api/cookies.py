"""The session cookie: its name and flags, by the scheme the browser used (SEC-010, 15.4).

A signed-in browser holds one opaque session token in a cookie:

- ``HttpOnly``, so page script can never read it;
- ``SameSite=Lax``: sent when a person follows a link from a ticket or a
  chat into vakr, never on a cross-site POST, frame, or fetch. ``Strict``
  would break those links for a server-rendered front end and protects
  nothing more here, because every state change also needs the CSRF token
  (SEC-011) and no GET changes state;
- ``Path=/``, no ``Domain``, and ``Max-Age`` equal to the session's
  remaining absolute lifetime, so the browser never keeps it longer than
  the server would accept it;
- over HTTPS (the scheme the trusted proxy reports, SEC-013) it is named
  ``__Host-vakr_session`` and marked ``Secure``: the prefix makes browsers
  refuse it from a sibling subdomain or over plain HTTP;
- over plain HTTP from loopback, for local testing without a proxy, it is
  ``vakr_session`` without ``Secure``, because browsers drop secure cookies
  on ``http://``. Plain HTTP from anywhere else gets no cookie at all: the
  login is refused.

Each scheme reads only its own cookie name, so a plain-HTTP cookie can
never stand in for the secure one.

Dependencies:
    starlette.responses: ``set_cookie``.
    vakr.api.proxy: The client view.

Safety:
    The token is revealed only into the ``Set-Cookie`` header itself.

Example:
    >>> from vakr.api.proxy import ClientView
    >>> cookie_name(ClientView("198.51.100.7", "https", "127.0.0.1"))
    '__Host-vakr_session'
"""

from datetime import datetime

from starlette.responses import Response

from vakr.api.proxy import ClientView
from vakr.credentials.secret import Secret

SECURE_COOKIE = "__Host-vakr_session"
PLAIN_COOKIE = "vakr_session"
MAX_TOKEN_LENGTH = 128
"""Longest cookie or bearer value considered; real tokens are 47 characters."""


def cookie_name(view: ClientView) -> str:
    """Return the session cookie name for the client's scheme.

    Args:
        view: The client after trusted-proxy handling.

    Returns:
        str: ``__Host-vakr_session`` over HTTPS, else ``vakr_session``.

    Raises:
        None
    """
    return SECURE_COOKIE if view.scheme == "https" else PLAIN_COOKIE


def cookie_allowed(view: ClientView) -> bool:
    """Tell whether a session cookie may be issued to this client.

    Args:
        view: The client.

    Returns:
        bool: True over HTTPS, or over plain HTTP from loopback (local testing).

    Raises:
        None
    """
    return view.scheme == "https" or view.loopback


def set_session_cookie(
    response: Response, token: Secret, view: ClientView, expires_at: datetime, now: datetime
) -> None:
    """Attach the session cookie to a response.

    Args:
        response: The login response.
        token: The new session token.
        view: The client.
        expires_at: The session's absolute expiry (UTC).
        now: Current time (UTC).

    Returns:
        None

    Raises:
        None
    """
    max_age = max(int((expires_at - now).total_seconds()), 0)
    response.set_cookie(cookie_name(view), token.reveal(), max_age=max_age, path="/",
                        secure=view.scheme == "https", httponly=True,
                        samesite="lax")  # fmt: skip


def clear_session_cookie(response: Response, view: ClientView) -> None:
    """Tell the browser to drop the session cookie.

    Args:
        response: The logout response.
        view: The client.

    Returns:
        None

    Raises:
        None
    """
    response.delete_cookie(cookie_name(view), path="/", secure=view.scheme == "https",
                           httponly=True, samesite="lax")  # fmt: skip
