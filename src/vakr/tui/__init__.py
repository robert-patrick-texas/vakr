"""The read-only terminal UI, ``vakr tui`` (blueprint 13, TUI-001..008, ADR-013).

Technical staff use it over ``ssh`` on the monitoring node: a device list
with Attention at the top, and a device screen with the interface table.
It reads through ``vakr.query.StatusQueries`` only (TUI-007): no SQL, no
adapter imports, and every read runs in a worker thread so the screen never
blocks on disk.

Modules:
    app: ``VakrTui``, the application.
    list_screen: The device list (TUI-002, TUI-003).
    device_screen: One device and its interfaces (TUI-004..006).
    modals: Help and health overlays.
    columns, tables: Column priorities for narrow terminals (TUI-008).

Dependencies:
    textual, rich: Terminal UI (ADR-013).
    vakr.query: The data.

Safety:
    Read-only; never reads secrets or shows credential references (TUI-010).

Example:
    >>> from vakr.tui.app import VakrTui
    >>> VakrTui.TITLE
    'vakr'
"""
