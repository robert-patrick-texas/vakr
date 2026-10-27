# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""What the field kit keeps from MRTG configuration, and where to find it.

``fieldkit_mrtgcfg`` parses MRTG's files into these records: each target
with its parsed ``Target[]`` value and the few keywords that change what
MRTG records, and the global settings that say where its data lives and
how often it polls.

Dependencies:
    fieldkit_mrtgtarget: ``TargetSpec``.
    glob, re: Locating files and splitting ``Options``.

Safety:
    ``MrtgConfig.secrets`` (communities and SNMPv3 users) is hidden from
    ``repr``; nothing here keeps raw configuration text.

Example:
    >>> interval_seconds("5"), interval_seconds("1:30"), interval_seconds("")
    (300, 90, 300)
"""

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

from fieldkit_mrtgtarget import TargetSpec

DEFAULT_PATHS = ("/etc/mrtg.cfg", "/etc/mrtg/*.cfg", "/usr/local/etc/mrtg/*.cfg")
"""Where MRTG configuration is found when ``--mrtg-config`` is not given."""


@dataclass
class MrtgTarget:
    """One graphed target.

    Attributes:
        name: The target name inside ``[]``.
        spec: The parsed ``Target[]`` value.
        values: Lower-cased kept keywords to values (``maxbytes``, ``options``...).
        source: ``file:line`` of the ``Target[]`` line.
    """

    name: str
    spec: TargetSpec
    values: dict[str, str]
    source: str

    def options(self) -> set[str]:
        """Return the target's ``Options`` words, lower-cased.

        Args:
            None

        Returns:
            set[str]: Such as ``{"bits", "growright"}``.

        Raises:
            None
        """
        return {w for w in re.split(r"[\s,]+", self.values.get("options", "").lower()) if w}


@dataclass
class MrtgConfig:
    """Everything kept from one or more configuration files.

    Attributes:
        files: Files read, in order.
        globals: Lower-cased global keywords to their last value.
        targets: Targets in definition order.
        notes: Problems found (file and line only).
        secrets: Communities and SNMPv3 users; hidden from ``repr``.
    """

    files: list[str] = field(default_factory=list)
    globals: dict[str, str] = field(default_factory=dict)
    targets: list[MrtgTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    secrets: set[str] = field(default_factory=set, repr=False)


def expand(pattern: str) -> list[Path]:
    """Expand a path whose last component may hold ``*`` or ``?`` wildcards.

    Args:
        pattern: Such as ``/etc/mrtg/*.cfg``.

    Returns:
        list[Path]: Matching paths, sorted; the path itself without wildcards.

    Raises:
        None
    """
    path = Path(pattern)
    if not glob.has_magic(path.name):
        return [path]
    return sorted(path.parent.glob(path.name)) if path.parent.is_dir() else []


def discover(explicit: list[str]) -> list[Path]:
    """Expand ``--mrtg-config`` values, or the default locations, to existing files.

    Args:
        explicit: Paths or wildcards given on the command line.

    Returns:
        list[Path]: Existing files, in order, without repeats.

    Raises:
        None
    """
    found = [p for pattern in (explicit or DEFAULT_PATHS) for p in expand(pattern)]
    return list(dict.fromkeys(p for p in found if p.is_file()))


def interval_seconds(text: str) -> int:
    """Convert ``Interval: MM[:SS]`` to seconds; MRTG's default is 5 minutes.

    Args:
        text: The value, or ``""``.

    Returns:
        int: Seconds, at least 1.

    Raises:
        None
    """
    minutes, _, seconds = text.strip().partition(":")
    try:
        total = int(float(minutes or 5) * 60) + int(seconds or 0)
    except ValueError:
        return 300
    return max(total, 1)
