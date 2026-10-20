"""Tell systemd about readiness and status (``Type=notify``, sd_notify(3)).

A ``Type=notify`` unit is "started" only once the process says
``READY=1``, so ``systemctl start vakr-collector`` returns after the
preflight passed and polling began, and a failed preflight shows as a
failed start rather than a service that ran and exited (SVC-004, SVC-005).
``STOPPING=1`` marks the graceful drain (16.4) and ``STATUS=`` puts one line
in ``systemctl status``.

The protocol is one datagram to the Unix socket named by
``$NOTIFY_SOCKET`` (an abstract socket when it starts with ``@``). The
socket is non-blocking, so a busy systemd can never stall the event loop;
a lost status line costs nothing.

Dependencies:
    os, socket: The environment variable and the datagram socket.

Safety:
    Sends only the fixed state strings the caller passes; no secrets.

Example:
    >>> notify("STATUS=polling 100 devices", environ={})
    False
"""

import os
import socket
from collections.abc import Mapping


def notify(state: str, environ: Mapping[str, str] | None = None) -> bool:
    """Send one notification to systemd, if it is listening.

    Args:
        state: Newline-separated ``KEY=value`` assignments, such as ``READY=1``.
        environ: Environment to read ``NOTIFY_SOCKET`` from; ``os.environ`` by default.

    Returns:
        bool: True when the datagram was sent; False without a socket or on error.

    Raises:
        None
    """
    address = (os.environ if environ is None else environ).get("NOTIFY_SOCKET", "")
    if not address or address[0] not in "@/":
        return False
    target = "\0" + address[1:] if address[0] == "@" else address
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.setblocking(False)
            sock.sendto(state.encode("utf-8"), target)
    except OSError:
        return False
    return True
