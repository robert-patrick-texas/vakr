# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""What the field kit keeps from SmokePing configuration, and where to find it.

``fieldkit_smokecfg`` parses SmokePing's files into these records: every
probed host with its probe and the hosts that probe it, the probes'
settings, and the Database step and pings.

Dependencies:
    dataclasses, pathlib: Standard library only.

Safety:
    ``SmokeConfig.secrets`` is hidden from ``repr``; nothing here keeps raw
    configuration text.

Example:
    >>> cfg = SmokeConfig(probes={"FPing": {"pings": "10"}, "Fast": {"_parent": "FPing"}})
    >>> cfg.probe_value("Fast", "pings", 20), cfg.probe_value("Fast", "step", 300)
    (10, 300)
"""

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATHS = ("/etc/smokeping/config", "/usr/local/etc/smokeping/config",
                 "/opt/smokeping/etc/config")  # fmt: skip


@dataclass
class SmokeTarget:
    """One probed host (a Targets node with ``host``).

    Attributes:
        path: Node names from the top of the tree, ``/``-joined.
        host: The ``host`` setting.
        probe: The inherited probe name.
        menu: ``menu`` text.
        title: ``title`` text.
        slaves: Inherited slave names that also probe it.
        master: False when ``nomasterpoll`` says the master does not probe it.
    """

    path: str
    host: str
    probe: str
    menu: str = ""
    title: str = ""
    slaves: tuple[str, ...] = ()
    master: bool = True


@dataclass
class SmokeConfig:
    """Everything kept from the configuration.

    Attributes:
        files: Files read.
        datadir: Where the RRD files are.
        step: Database step, seconds.
        pings: Database pings per round.
        probes: Probe (and subprobe) name to its kept settings.
        targets: Probed hosts in tree order.
        notes: Problems found (file and line only).
        secrets: Values of credential-like settings; hidden from ``repr``.
    """

    files: list[str] = field(default_factory=list)
    datadir: str = ""
    step: int = 300
    pings: int = 20
    probes: dict[str, dict[str, str]] = field(default_factory=dict)
    targets: list[SmokeTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    secrets: set[str] = field(default_factory=set, repr=False)

    def probe_value(self, probe: str, key: str, fallback: int) -> int:
        """Return a probe's numeric setting, inheriting from its parent probe.

        Args:
            probe: Probe or subprobe name.
            key: ``pings`` or ``step``.
            fallback: The Database value.

        Returns:
            int: The value.

        Raises:
            None
        """
        settings = self.probes.get(probe, {})
        text = settings.get(key) or self.probes.get(settings.get("_parent", ""), {}).get(key)
        return int(text) if text and text.isdigit() else fallback


def discover(explicit: list[str]) -> list[Path]:
    """Return ``--smokeping-config`` files, or the first default that exists.

    Args:
        explicit: Paths given on the command line.

    Returns:
        list[Path]: Existing files.

    Raises:
        None
    """
    if explicit:
        return [Path(p) for p in explicit if Path(p).is_file()]
    return [Path(p) for p in DEFAULT_PATHS if Path(p).is_file()][:1]
