"""Clients of the local ``vakr-api`` socket: ``vakr status`` and ``vakr tui`` (13.1, TUI-007).

On the monitoring node the status views read through the running API, over
``/run/vakr/api.sock``. The socket authorizes by account (E.12, E.30 #2):
root and members of ``vakr-admin``, ``vakr-view``, or ``vakr`` are served, so no password,
cookie, or token is ever sent.

Modules:
    transport: One bounded HTTP/1.1 ``GET`` over a Unix socket.
    answers: Error envelopes mapped to the exceptions the local path raises.
    status: ``SocketStatusSource``, the ``vakr.query.StatusSource`` over the API.

Dependencies:
    http.client, json, socket, threading: Standard library only.
    vakr.query, vakr.domain: The views, the decoder, and the cursor codec.
    No web framework, server, or adapter is imported, so the commands start
    quickly and the TUI keeps TUI-T05's boundary.

Safety:
    Read-only ``GET`` requests; every connection, answer, and decoding is
    bounded (Appendix E.29).

Example:
    >>> from vakr.client.status import SocketStatusSource
    >>> SocketStatusSource("/run/vakr/api.sock").socket_path
    '/run/vakr/api.sock'
"""
