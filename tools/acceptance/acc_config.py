# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Read ``acceptance.toml``: the tolerances and alignment rules of the comparison.

The parallel-run comparison is acceptance evidence, so its rules are data
a reviewer can read and a project can change without touching Python:
the traffic tolerance and window length (MVP-ACC-03), the latency
tolerance and loss-consistency test (MVP-ACC-04), how far MRTG's poll phase
is searched, how much of a window the application's samples must cover,
and how many windows make a verdict. vakr's values are in blueprint
Appendix E.34.

Dependencies:
    tomllib, dataclasses: Standard library only.

Safety:
    Reads one local file. Values outside their ranges are refused with
    the key named.

Example:
    >>> rules = rules_from({"traffic": {"tolerance": 0.02}})
    >>> rules.traffic.tolerance, rules.latency.tolerance_ms
    (0.02, 1.0)
"""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """The configuration file is missing, malformed, or out of range."""


@dataclass(frozen=True, slots=True)
class TrafficRules:
    """MVP-ACC-03 rules.

    Attributes:
        tolerance: Relative agreement required per window (0.02 is +-2 %).
        step_s: MRTG window length compared.
        min_coverage: Share of a window the application's samples must cover.
        phase_search_s: Phase searched either side of MRTG's last-run phase.
        phase_step_s: Phase search step.
        jitter_s: Run-to-run start variation that explains a window.
        quantum_bps: MRTG ``.log`` resolution (whole bytes per second).
        min_windows: Compared windows needed for a verdict.
    """

    tolerance: float = 0.02
    step_s: int = 300
    min_coverage: float = 0.9
    phase_search_s: int = 60
    phase_step_s: int = 5
    jitter_s: int = 30
    quantum_bps: float = 8.0
    min_windows: int = 288


@dataclass(frozen=True, slots=True)
class LatencyRules:
    """MVP-ACC-04 rules.

    Attributes:
        tolerance_ms: Absolute RTT agreement.
        tolerance_rel: Relative RTT agreement; the larger of the two applies.
        loss_abs: Loss rates agreeing within this absolute share are consistent.
        loss_z: Or within this many standard errors of their difference.
        high_loss: Share of lost probes above which a window's median is weak.
        min_windows: Compared windows needed for a verdict.
    """

    tolerance_ms: float = 1.0
    tolerance_rel: float = 0.05
    loss_abs: float = 0.005
    loss_z: float = 3.0
    high_loss: float = 0.5
    min_windows: int = 288


@dataclass(frozen=True, slots=True)
class Rules:
    """Every rule of one report.

    Attributes:
        title: Report title.
        inbox: Directory holding extracted bundles.
        max_bundles: Most bundles read.
        max_listed: Out-of-tolerance windows listed per subject in Markdown.
        traffic: MVP-ACC-03 rules.
        latency: MVP-ACC-04 rules.
    """

    title: str = "Parallel run: comparison with MRTG and SmokePing"
    inbox: Path = Path("inbox")
    max_bundles: int = 1000
    max_listed: int = 40
    traffic: TrafficRules = field(default_factory=TrafficRules)
    latency: LatencyRules = field(default_factory=LatencyRules)


_RANGES = {
    "tolerance": (0.0, 1.0), "step_s": (60, 3600), "min_coverage": (0.0, 1.0),
    "phase_search_s": (0, 3600), "phase_step_s": (1, 300), "jitter_s": (0, 600),
    "quantum_bps": (0.0, 1e6), "min_windows": (1, 1_000_000), "tolerance_ms": (0.0, 1000.0),
    "tolerance_rel": (0.0, 1.0), "loss_abs": (0.0, 1.0), "loss_z": (0.0, 10.0),
    "high_loss": (0.0, 1.0), "max_bundles": (1, 100_000), "max_listed": (0, 10_000),
}  # fmt: skip


def _section(kind: type[Any], data: dict[str, Any], name: str) -> Any:
    """Build one rules dataclass from its table, checking names and ranges.

    Args:
        kind: The dataclass.
        data: The TOML table.
        name: Table name, for messages.

    Returns:
        Any: An instance of ``kind``.

    Raises:
        ConfigError: For an unknown key, a wrong type, or a value out of range.
    """
    known = {f.name: f for f in fields(kind)}
    values: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(f"[{name}] {key}: unknown setting")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigError(f"[{name}] {key}: must be a number")
        low, high = _RANGES[key]
        if not low <= value <= high:
            raise ConfigError(f"[{name}] {key}: must be between {low} and {high}")
        values[key] = int(value) if isinstance(known[key].default, int) else float(value)
    return kind(**values)


def rules_from(data: dict[str, Any], base: Path = Path()) -> Rules:
    """Build the rules from parsed TOML.

    Args:
        data: The whole file.
        base: Directory relative paths are resolved from.

    Returns:
        Rules: The rules.

    Raises:
        ConfigError: If a value is invalid.
    """
    report = dict(data.get("report", {}))
    title = str(report.pop("title", Rules.title))
    inbox = base / str(report.pop("inbox", "inbox"))
    limits = _section(_Limits, report, "report")
    return Rules(title, inbox, limits.max_bundles, limits.max_listed,
                 _section(TrafficRules, data.get("traffic", {}), "traffic"),
                 _section(LatencyRules, data.get("latency", {}), "latency"))  # fmt: skip


@dataclass(frozen=True, slots=True)
class _Limits:
    """The numeric ``[report]`` settings.

    Attributes:
        max_bundles: Most bundles read.
        max_listed: Windows listed per subject.
    """

    max_bundles: int = 1000
    max_listed: int = 40


def load_rules(path: Path) -> Rules:
    """Read the configuration file.

    Args:
        path: ``acceptance.toml``.

    Returns:
        Rules: The rules.

    Raises:
        ConfigError: If it cannot be read or is invalid.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return rules_from(data, path.resolve().parent)
