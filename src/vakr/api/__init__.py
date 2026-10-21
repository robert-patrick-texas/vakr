"""The ``vakr-api`` service: JSON API v1 over loopback TCP and a local Unix socket (6.2, 14).

``vakr-api.service`` runs ``vakr api serve``. The process reads telemetry
(the collector is its only writer) and writes metadata: sessions, audit
records, and administrator decisions. A reverse proxy terminates TLS in
front of the loopback listener (SEC-013); ``vakr tui`` and the CLI use the
Unix socket ``/run/vakr/api.sock``, authorized by the connecting account
(E.12). Every endpoint answers JSON, so a server-rendered or a single-page
web front end can be built on the API alone; ``create_app`` has one named
hook, ``web``, where that front end is attached later.

Modules:
    settings: ``[api]`` options and fixed bounds (E.30).
    context, build: The services one process shares, and their composition root.
    app, middleware, proxy, errors: The FastAPI app, request plumbing, trusted
        proxies (SEC-013), and the error envelope (API-007).
    principals, cookies, peer: Who is asking: session cookie, kiosk token, or
        socket peer; CSRF (SEC-010, SEC-011).
    routes_*: The endpoints; ``schemas``, ``paging``, ``params`` their shapes.
    openapi, server, listeners: Schema generation (API-008) and the service runner.

Dependencies:
    fastapi, uvicorn: HTTP; imported only by the modules that need them, so
        ``vakr.api.settings`` stays cheap for ``vakr config validate``.

Safety:
    Passwords, session, kiosk, and CSRF tokens never reach a log line, an
    error body, or a repr. Every query, page, body, and export is bounded.

Example:
    >>> from vakr.api.settings import ApiSettings
    >>> ApiSettings().port
    8080
"""
