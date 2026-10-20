"""Help and health overlays for the TUI (13.2 ``?`` and ``h``, TUI-009).

The help overlay lists the keys and explains the states in operator words
(18.3): what the icons mean, that "Unknown" is not "Down", that "—" is not
zero. The health overlay shows whether inventory and telemetry could be
read and each collector's latest self-report, so collector or storage
trouble is never mistaken for a network problem (OBS-002).

Dependencies:
    textual: Modal screens and widgets.
    vakr.query: ``HealthView``.

Safety:
    Display only.

Example:
    >>> "Unknown is not Down" in HELP_TEXT
    True
"""

from typing import ClassVar

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from vakr.query import HealthView

HELP_TEXT = """[b]Keys[/b]
  /  search      f  filter (all, attention, active, offline)      s  sort
  r  refresh     Enter  open device     Escape  back     h  health     q  quit
  ↑ ↓ PageUp PageDown  move      ]  \\[  next / previous page of 500 devices

[b]States[/b] (colour is never the only cue)
  ✖ Down        no reply of any kind from a healthy collector (red)
  ▲ degraded    packet loss, latency, SNMP failing, partial data, restarted (amber)
  ? Unknown     vakr cannot see the device now: stale data, collector trouble,
                or storage trouble (amber). Unknown is not Down.
  ✔ OK          fresh evidence and no current problem (green)
  ○ ◐ offline   disabled, retired, or in maintenance (gray)

[b]Values[/b]
  —             not available; the device screen says why. It is never zero.
  (stale)       older than the freshness threshold: 2.5 polls, at least poll + 30 s.
  sysUpTime     how long the SNMP agent has run, not proof of chassis uptime.

Press Escape to close."""


class HelpScreen(ModalScreen[None]):
    """Keys and state legend."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        """Show the help text.

        Args:
            None

        Returns:
            ComposeResult: The widgets.

        Raises:
            None
        """
        with Vertical(classes="overlay"):
            yield Static(HELP_TEXT, id="help-text")


def health_text(health: HealthView | None, error: str | None) -> str:
    """Describe storage and collector health, one item per line.

    Args:
        health: The latest health view, or ``None`` before the first load.
        error: Why the last refresh failed, if it did.

    Returns:
        str: Console markup, values escaped.

    Raises:
        None
    """
    lines = ["[b]Health[/b]"]
    if error:
        lines.append(f"[bold yellow]last refresh failed: {escape(error)}[/]")
    if health is None:
        return "\n".join([*lines, "no data loaded yet", "", "Press Escape to close."])
    lines.append(f"  metadata database   {escape(health.metadata.text)}")
    lines.append(f"  telemetry database  {escape(health.telemetry.text)}")
    if not health.collectors:
        lines.append("  no collector registered yet")
    for c in health.collectors:
        state = c.state.text if c.state.value is not None else f"— ({c.state.reason})"
        reported = c.age.value is not None
        detail = f", report {c.age.text} old, spool {c.spool.text}" if reported else ""
        lines.append(f"  collector {escape(c.position):<9} {escape(state)}{escape(detail)}")
    lines += ["", "Collector or storage trouble makes devices Unknown, never Down."]
    return "\n".join([*lines, "", "Press Escape to close."])


class HealthScreen(ModalScreen[None]):
    """Collector, database, and spool health (TUI-009)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("h", "dismiss", "Close"),
    ]

    def __init__(self, health: HealthView | None, error: str | None) -> None:
        """Hold the health to show.

        Args:
            health: The latest health view, if any.
            error: Why the last refresh failed, if it did.

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self._text = health_text(health, error)

    def compose(self) -> ComposeResult:
        """Show the health text.

        Args:
            None

        Returns:
            ComposeResult: The widgets.

        Raises:
            None
        """
        with Vertical(classes="overlay"):
            yield Static(self._text, id="health-text")
