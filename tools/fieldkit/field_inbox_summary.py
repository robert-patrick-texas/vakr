# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Summarize an extracted field bundle for the developer (FIELD-008).

After extraction the developer wants the essentials at a glance before
opening any file: which host and tool version produced the bundle, which
profiles ran, whether it was pseudonymized, how many devices were walked,
what the GitHub reachability test found (including the issuer of the
certificate the production host was shown), and which steps failed.

Dependencies:
    json: Reads ``manifest.json`` (already verified) and ``host/egress.json``.

Safety:
    Read-only; prints only what the bundle already contains.

Example:
    >>> egress_line({"method": "GET", "url": "https://x", "http": {"status": 200}})
    'GET https://x: HTTP 200'
"""

import json
from pathlib import Path
from typing import Any

MAX_ERRORS_SHOWN = 10


def egress_line(record: dict[str, Any]) -> str:
    """Describe one reachability probe in one line.

    Args:
        record: One entry of ``host/egress.json``.

    Returns:
        str: Method, URL, and HTTP status or the failing stage, plus the
        presented certificate issuer when verification failed.

    Raises:
        None
    """
    head = f"{record.get('method')} {record.get('url')}"
    if "error" not in record:
        return f"{head}: HTTP {record.get('http', {}).get('status')}"
    presented = record.get("tls", {}).get("presented", {})
    issuer = f" (presented issuer: {presented['issuer']})" if "issuer" in presented else ""
    return f"{head}: {record['error']}{issuer}"


def summarize(dest: Path, manifest: dict[str, Any]) -> str:
    """Render a multi-line summary of an extracted bundle.

    Args:
        dest: Extraction directory.
        manifest: Verified manifest.

    Returns:
        str: Summary text.

    Raises:
        None
    """
    tool = manifest.get("tool", {})
    members = manifest.get("members", {})
    size = sum(int(m.get("bytes", 0)) for m in members.values())
    devices = {name.split("/")[1] for name in members if name.startswith("snmp/")}
    pseudo = manifest.get("pseudonymization", {}).get("enabled", False)
    lines = [
        f"field-inbox: {manifest.get('bundle_id')} -> {dest}",
        f"  created {manifest.get('created_utc')} by {tool.get('name')} {tool.get('version')}"
        f" (Python {tool.get('python')})",
        f"  profiles: {', '.join(manifest.get('profiles', []))}; pseudonymized: {pseudo}",
        f"  members: {len(members)} ({size} bytes); SNMP devices: {len(devices)}",
    ]
    egress = dest / "host" / "egress.json"
    if egress.is_file():
        try:
            records = json.loads(egress.read_text(encoding="utf-8"))
        except ValueError:
            records = []
        lines += [f"  egress: {egress_line(r)}" for r in records if isinstance(r, dict)]
    errors = manifest.get("errors", [])
    lines.append(f"  step errors: {len(errors)}")
    lines += [f"    {e}" for e in errors[:MAX_ERRORS_SHOWN]]
    if len(errors) > MAX_ERRORS_SHOWN:
        lines.append(f"    ... {len(errors) - MAX_ERRORS_SHOWN} more in manifest.json")
    lines += [f"  note: {n}" for n in manifest.get("notes", [])[:MAX_ERRORS_SHOWN]]
    return "\n".join(lines)
