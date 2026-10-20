"""Remove known secret values from text and log records (SEC-003, SEC-020).

``Secret`` keeps values out of reprs and messages by construction, but a
value that was revealed for a library call can still come back in that
library's exception text or a careless log argument. ``redact_text`` replaces
every known value, including its ``repr``, JSON, and percent-encoded forms
(a DSN password inside a URL), with ``***``. ``RedactingFilter`` applies it to
whole log records: the formatted message with its arguments, exception and
stack text, and string ``extra`` fields. Support bundles will use
``redact_text`` as their last line of defence behind a field allowlist.

Dependencies:
    logging: The ``Filter`` interface.
    vakr.credentials.secret: Values to redact.

Safety:
    Attach the filter to every *handler*: a filter on a logger does not see
    records propagated from child loggers. The filter always lets records
    through; it changes them in place, so every later handler sees the
    redacted record. Values rotated out of the provider are no longer known.

Example:
    >>> redact_text("community=placeholder", [Secret("placeholder")])
    'community=***'
"""

import json
import logging
import traceback
import urllib.parse
from collections.abc import Callable, Iterable

from vakr.credentials.secret import Secret

MASK = "***"
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}

type SecretSource = Callable[[], Iterable[Secret | str]]
"""Returns the secrets to redact now, such as ``provider.known_secrets``."""


def _variants(value: str) -> set[str]:
    """Return the forms in which a value commonly appears in text.

    Args:
        value: A secret value.

    Returns:
        set[str]: Plain, ``repr``-escaped, JSON-escaped, and percent-encoded
        forms, without empty strings.

    Raises:
        None
    """
    forms = {
        value,
        repr(value)[1:-1],
        json.dumps(value)[1:-1],
        urllib.parse.quote(value, safe=""),
    }
    return {form for form in forms if form}


def redact_text(text: str, secrets: Iterable[Secret | str]) -> str:
    """Replace every known secret value in ``text`` with ``***``.

    Args:
        text: Text that may contain secrets.
        secrets: Known secrets, wrapped or plain.

    Returns:
        str: The text with each value and its escaped forms replaced,
        longest first, so a secret that contains another is fully masked.

    Raises:
        None
    """
    forms: set[str] = set()
    for item in secrets:
        forms |= _variants(item.reveal() if isinstance(item, Secret) else item)
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, MASK)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that removes known secrets from every record it sees.

    Attributes:
        None public; secrets come from the source given at construction.
    """

    def __init__(self, source: SecretSource, name: str = "") -> None:
        """Create the filter.

        Args:
            source: Callable returning the secrets to redact; called for each
                record, so rotation is picked up without re-installing.
            name: ``logging.Filter`` name restriction; empty passes all.

        Returns:
            None

        Raises:
            None
        """
        super().__init__(name)
        self._source = source

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place.

        Args:
            record: The record being emitted.

        Returns:
            bool: Always ``True``; redaction never drops a record.

        Raises:
            None
        """
        known = tuple(self._source())
        if not known:
            return True
        record.msg = redact_text(_message(record), known)
        record.args = ()
        if record.exc_info:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            # Handlers that format exc_info themselves would print the original.
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text, known)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info, known)
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS and isinstance(value, str):
                setattr(record, key, redact_text(value, known))
        return True


def _message(record: logging.LogRecord) -> str:
    """Format a record's message with its arguments, tolerating bad arguments.

    Args:
        record: The record.

    Returns:
        str: ``record.getMessage()``, or the message and argument reprs when
        the arguments do not fit the format string.

    Raises:
        None
    """
    try:
        return record.getMessage()
    except TypeError, ValueError:
        return f"{record.msg} {record.args!r}"
