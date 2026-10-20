"""Structured events with stable IDs, sent to journald and echoed to the console.

Every update step emits an event with a stable ID (UPD-007, SVC-006) so an
operator can run ``journalctl EVENT_ID=update.failed`` across hosts, and so
audit-worthy changes such as trusting a new CA leave a record. Events go to
journald's native socket when it exists; on the console the human message is
printed as well, and when journald is absent (containers, tests) the
console line includes the ID.

Dependencies:
    socket: Datagrams to ``/run/systemd/journal/socket``.

Safety:
    Callers pass only versions, paths, names, and fingerprints, never
    secrets. Messages are bounded in size. A failure to reach journald never
    fails the operation being logged.

Example:
    >>> sink = EventSink("vakr-update", Path("/nonexistent"), echo=print)
    >>> sink("update.check", "checking for updates")
    update.check: checking for updates
"""

import socket
import struct
from collections.abc import Callable
from pathlib import Path

JOURNAL_SOCKET = Path("/run/systemd/journal/socket")
MAX_VALUE = 4096
PRIORITY_INFO = 6
PRIORITY_WARNING = 4
PRIORITY_ERROR = 3


class EventSink:
    """Callable event emitter; one instance per command invocation.

    Not thread-safe; the updater is single-threaded.
    """

    def __init__(
        self,
        identifier: str,
        socket_path: Path | None = None,
        echo: Callable[[str], None] | None = print,
        echo_always: bool = True,
    ) -> None:
        """Configure where events go.

        Args:
            identifier: ``SYSLOG_IDENTIFIER`` for journald, e.g. ``vakr-update``.
            socket_path: journald native socket; ``None`` means
                ``JOURNAL_SOCKET`` looked up at send time.
            echo: Console printer, or ``None`` for journald only.
            echo_always: Echo even when journald received the event; timer
                runs pass ``False`` so the journal is not written twice via
                the unit's captured stdout.

        Returns:
            None

        Raises:
            None
        """
        self.identifier = identifier
        self.socket_path = socket_path
        self.echo = echo
        self.echo_always = echo_always

    def __call__(
        self, event_id: str, message: str, priority: int = PRIORITY_INFO, **extra: str
    ) -> None:
        """Emit one event.

        Args:
            event_id: Stable dotted ID such as ``update.install.done``.
            message: Human-readable message.
            priority: syslog priority (3 error, 4 warning, 6 info).
            **extra: Additional upper-case journald fields.

        Returns:
            None

        Raises:
            None
        """
        fields = {
            "MESSAGE": message,
            "PRIORITY": str(priority),
            "SYSLOG_IDENTIFIER": self.identifier,
            "EVENT_ID": event_id,
            **{k.upper(): v for k, v in extra.items()},
        }
        sent = self._send(fields)
        if self.echo is not None and (self.echo_always or not sent):
            self.echo(message if sent else f"{event_id}: {message}")

    def _send(self, fields: dict[str, str]) -> bool:
        """Deliver fields to journald using its native datagram protocol.

        Args:
            fields: Journal field names and values.

        Returns:
            bool: ``True`` when journald accepted the datagram.

        Raises:
            None
        """
        target = self.socket_path or JOURNAL_SOCKET
        if not target.is_socket():
            return False
        payload = b"".join(_encode(k, v[:MAX_VALUE]) for k, v in fields.items())
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.sendto(payload, str(target))
        except OSError:
            return False
        return True


def _encode(name: str, value: str) -> bytes:
    """Encode one field; values with newlines use the length-prefixed form.

    Args:
        name: Upper-case field name.
        value: Field value.

    Returns:
        bytes: Serialized field.

    Raises:
        None
    """
    data = value.encode("utf-8", "replace")
    if b"\n" not in data:
        return name.encode() + b"=" + data + b"\n"
    return name.encode() + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"
