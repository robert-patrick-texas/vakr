# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Shared state of one collection run, handed to every profile (FIELD-002).

Profiles are plain functions that take a ``Context``: they read options from
it, run steps through its step log, and add members to its bundle. Keeping
that shape uniform is what lets a new data need become a new profile rather
than a new tool.

Dependencies:
    fieldkit_bundle: The open bundle writer.
    fieldkit_run: Step log, build information, and bounded commands.
    fieldkit_scrub, fieldkit_mibscrub, fieldkit_tablescrub: Pseudonymization hooks.

Safety:
    ``capture`` runs commands from argument lists with a timeout and stores
    their output only through the bundle writer, which redacts credentials
    and enforces size caps.

Example:
    >>> make_scrub_hook(None) is None
    True
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from fieldkit_bundle import BundleWriter, safe_label
from fieldkit_mibscrub import scrub_walk
from fieldkit_run import BuildInfo, StepLog, run_command
from fieldkit_scrub import Scrubber
from fieldkit_tablescrub import scrub_table


def make_scrub_hook(scrubber: Scrubber | None) -> Callable[[str, str], str] | None:
    """Build the bundle writer's ``(kind, text) -> text`` pseudonymization hook.

    Args:
        scrubber: The run's scrubber, or ``None`` without ``--pseudonymize``.

    Returns:
        Callable[[str, str], str] | None: Hook applying walk-aware rules to
        ``"snmpwalk"`` members, column rules to ``"table"`` members, and
        generic rules to everything else.

    Raises:
        None
    """
    if scrubber is None:
        return None

    def hook(kind: str, text: str) -> str:
        """Pseudonymize one member's text.

        Args:
            kind: ``"snmpwalk"``, ``"table"`` (CSV with a header), or ``"text"``.
            text: Member content.

        Returns:
            str: Pseudonymized content.

        Raises:
            None
        """
        if kind == "table":
            return scrub_table(scrubber, text)
        return scrub_walk(scrubber, text) if kind == "snmpwalk" else scrubber.text(text)

    return hook


@dataclass
class Context:
    """Everything a collection profile needs; one per run.

    Attributes:
        args: Parsed ``collect`` options.
        info: Embedded build information.
        bundle: Open bundle writer.
        steps: Step timing and error log.
        scrubber: Pseudonymization state with ``--pseudonymize``, else ``None``.
        devices: Validated entries from ``--devices``.
        community: SNMPv2c community; excluded from ``repr`` so it cannot
            leak through a traceback or debug print.
        parsed: Results profiles hand to later ones: parsed MRTG and
            SmokePing configuration, host aliases, and comparison targets.
    """

    args: argparse.Namespace
    info: BuildInfo
    bundle: BundleWriter
    steps: StepLog
    scrubber: Scrubber | None = None
    devices: list[str] = field(default_factory=list)
    community: str = field(default="", repr=False)
    parsed: dict[str, Any] = field(default_factory=dict)

    def label(self, name: str) -> str:
        """Return the safe, pseudonymized-if-enabled path label for a host.

        Args:
            name: Host name or address.

        Returns:
            str: Label for member paths and manifest entries.

        Raises:
            None
        """
        return safe_label(self.scrubber.p.hostname(name) if self.scrubber else name)

    def capture(self, step: str, member: str, argv: Sequence[str], timeout: float) -> int | None:
        """Run a command as one step and store its output as one member.

        Args:
            step: Step name for the manifest.
            member: Member path for stdout; stderr is appended if present.
            argv: Command argument list.
            timeout: Seconds before the command is killed.

        Returns:
            int | None: Exit status, or ``None`` if it did not run or finish.

        Raises:
            None
        """
        result = None
        with self.steps.step(step) as record:
            result = run_command(argv, timeout)
            record["returncode"] = result.returncode
            if result.error or result.returncode:
                record["error"] = result.error or f"exit status {result.returncode}"
            text = result.stdout + (f"\n--- stderr ---\n{result.stderr}" if result.stderr else "")
            if text:
                self.bundle.add_text(member, text)
        return result.returncode if result else None
