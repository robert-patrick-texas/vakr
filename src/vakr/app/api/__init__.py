"""``vakr-api.service``: the composition root and runner of the API process (6.2, 16.3, 16.4).

``vakr.api`` is the HTTP layer and sees only ports and query services; this
package decides which adapters fill them (SQLite, read-only telemetry),
runs the startup checks, opens the two listeners, and runs uvicorn until
SIGTERM.

Modules:
    build: Startup checks and the services (``build_parts``).
    readonly: The telemetry view that refuses every write.
    listeners: The loopback TCP socket and the local Unix socket.
    server: The uvicorn servers, readiness notification, session purge, shutdown.

Dependencies:
    vakr.api, vakr.adapters.sqlite, uvicorn: The parts.

Safety:
    Blocking work runs in worker threads; the API never writes telemetry.

Example:
    >>> import vakr.app.api
    >>> vakr.app.api.__name__
    'vakr.app.api'
"""
