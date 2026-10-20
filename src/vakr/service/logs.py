"""journald-friendly logging for vakr services (SVC-006, 17.9).

A unit's stderr goes to journald, which reads a ``<N>`` prefix on each line
as the syslog priority. Each line therefore starts with the priority, then
the stable event ID (``journalctl -u vakr-collector | grep
'[collector.preflight_failed]'``), the component, and the message:

    <4>[discovery.no_result] vakr.app.collector.discovery: discovery of core1 got ...

journald adds the timestamp and unit. A ``RedactingFilter`` on the handler
removes any loaded secret from every line as a second line of defence
(SEC-003); first-line defence is that no code formats a secret at all.

Dependencies:
    logging: The standard handler and formatter.
    vakr.credentials: ``RedactingFilter``.

Safety:
    Messages never include secrets by construction; the filter is a backstop.

Example:
    >>> PRIORITIES[logging.WARNING]
    4
"""

import logging
import sys
from typing import TextIO

from vakr.credentials import RedactingFilter
from vakr.credentials.redact import SecretSource

PRIORITIES = {
    logging.CRITICAL: 2,
    logging.ERROR: 3,
    logging.WARNING: 4,
    logging.INFO: 6,
    logging.DEBUG: 7,
}
"""syslog priorities journald reads from a ``<N>`` line prefix."""


class JournalFormatter(logging.Formatter):
    """Formats ``<priority>[event_id] logger: message`` lines."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a single journald line.

        Args:
            record: The log record; ``event_id`` comes from ``extra``.

        Returns:
            str: The line; newlines in the message are flattened.

        Raises:
            None
        """
        priority = PRIORITIES.get(record.levelno, 6)
        event = getattr(record, "event_id", None) or "log"
        text = record.getMessage()
        if record.exc_info and record.exc_info[0] is not None:
            text += f" ({record.exc_info[0].__name__})"  # type only: tracebacks may quote values
        return f"<{priority}>[{event}] {record.name}: {text}".replace("\n", " | ")


def configure_service_logging(
    secrets: SecretSource, *, level: int = logging.INFO, stream: TextIO | None = None
) -> logging.Handler:
    """Send vakr's log records to stderr in journald form.

    Args:
        secrets: Returns the secrets to redact, such as ``provider.known_secrets``.
        level: Lowest level written.
        stream: Output stream; ``sys.stderr`` by default.

    Returns:
        logging.Handler: The installed handler, so callers can remove it.

    Raises:
        None
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JournalFormatter())
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger("vakr")
    root.addHandler(handler)
    root.setLevel(level)
    # pysnmp and asyncio are chatty at DEBUG; their warnings still pass.
    logging.getLogger("pysnmp").setLevel(logging.WARNING)
    return handler
