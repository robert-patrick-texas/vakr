"""``vakr status --json``: a stable, versioned JSON form of the status views (TIME-004).

The document is the view model itself, converted mechanically, so the JSON
and the text table can never disagree:

- every ``Reading`` becomes ``{"text", "value", "reason", "stale"}``; a
  missing value is ``"value": null`` with a ``reason``, never ``0``;
- enums become their stable lowercase values; times become ISO 8601 UTC
  with ``Z``; UUIDs become strings;
- the top level carries ``"schema": "vakr.status/1"``, ``"generated_at"``,
  ``"exit_code"``, and either ``"devices"`` or ``"device"``.

A change that removes or renames a key is a new schema number.

Dependencies:
    dataclasses, enum, json, uuid: Mechanical conversion.
    vakr.query: The views.

Safety:
    Views hold no secrets or credential references, so nothing needs redacting.

Example:
    >>> from vakr.query import Reading
    >>> plain(Reading("—", None, "first sample"))
    {'text': '—', 'value': None, 'reason': 'first sample', 'stale': False}
"""

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from vakr.query import DeviceDetailView, DeviceListView
from vakr.query.text import utc_iso

SCHEMA = "vakr.status/1"
"""Version of the ``--json`` document; bump it when a key is removed or renamed."""


def plain(value: Any) -> Any:
    """Convert a view model into JSON-ready values.

    Args:
        value: A dataclass, tuple, enum, datetime, UUID, or scalar.

    Returns:
        Any: Dicts, lists, strings, numbers, booleans, and ``None``.

    Raises:
        None
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple | list):
        return [plain(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def list_document(view: DeviceListView, exit_code: int) -> str:
    """Render the device list as JSON.

    Args:
        view: The device list.
        exit_code: The command's exit status, repeated for scripts.

    Returns:
        str: The JSON document, indented, one trailing newline.

    Raises:
        None
    """
    body = plain(view)
    doc = {
        "schema": SCHEMA,
        "generated_at": body.pop("generated_at"),
        "exit_code": exit_code,
        "devices": body.pop("rows"),
        **body,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def detail_document(view: DeviceDetailView, exit_code: int) -> str:
    """Render one device as JSON; ``facts`` become ``{"label", ...reading}`` objects.

    Args:
        view: The device detail.
        exit_code: The command's exit status.

    Returns:
        str: The JSON document.

    Raises:
        None
    """
    body = plain(view)
    body["facts"] = [{"label": label, **plain(r)} for label, r in view.facts]
    doc = {
        "schema": SCHEMA,
        "generated_at": body.pop("generated_at"),
        "exit_code": exit_code,
        "device": body.pop("row"),
        **body,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def error_document(message: str, exit_code: int) -> str:
    """Render a failure (no data yet, storage unreadable) as JSON.

    Args:
        message: Operator explanation.
        exit_code: The command's exit status.

    Returns:
        str: ``{"schema", "exit_code", "error"}``.

    Raises:
        None
    """
    doc = {"schema": SCHEMA, "exit_code": exit_code, "error": message}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
