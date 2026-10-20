"""Long-running service plumbing shared by vakr's systemd units (SVC-001..006).

- ``sdnotify``: readiness and status for ``Type=notify`` units, over
  ``$NOTIFY_SOCKET`` with the standard library only.
- ``signals``: SIGTERM/SIGINT to graceful stop, SIGHUP to reload (CONF-003).
- ``logs``: journald-friendly log lines with priority prefixes, stable
  event IDs, and secret redaction (SVC-006).

Dependencies:
    The modules listed above; standard library only, plus the redaction filter.

Safety:
    Nothing here blocks the event loop: notifications are non-blocking
    datagrams, and handlers only set flags.

Example:
    >>> from vakr.service.sdnotify import notify
    >>> notify("READY=1", environ={})
    False
"""
