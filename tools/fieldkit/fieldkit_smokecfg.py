# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Read SmokePing configuration: the Targets tree, probes, step, pings, and datadir.

The ``smokeping`` profile compares SmokePing's median RTT and loss with
vakr's, so it needs every probed host with its probe, the number of pings
per round, the step, and where the RRD files live. The Debian and Ubuntu
package splits the configuration into ``/etc/smokeping/config`` and
``config.d/*`` files joined with ``@include``. Sections are ``*** Name ***``,
subsections ``+ name``, ``++ name``, and so on, and settings are ``key = value``.
``probe``, ``slaves``, and ``nomasterpoll`` are inherited down the Targets
tree. Only allow-listed settings are kept: probe passwords, RADIUS secrets,
URLs with credentials, alert addresses, and the slave secrets file are
ignored, and any value of a credential-like setting is returned in
``secrets`` so the bundle writer redacts it if it ever appears.

Dependencies:
    fieldkit_smokemodel: The records.
    re, pathlib: Standard library.

Safety:
    Reads at most ``MAX_FILES`` files and ``MAX_BYTES`` bytes, follows
    ``@include`` at most ``MAX_DEPTH`` deep, and never writes.

Example:
    >>> text = "*** Targets ***\nprobe = FPing\n+ Core\n++ r1\nhost = 192.0.2.1\n"
    >>> [(t.path, t.host, t.probe) for t in parse_text(text, "config").targets]
    [('Core/r1', '192.0.2.1', 'FPing')]
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from fieldkit_smokemodel import SmokeConfig, SmokeTarget

MAX_FILES = 100
MAX_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 8
TARGET_KEYS = frozenset({"host", "probe", "menu", "title", "slaves", "nomasterpoll"})
INHERITED = ("probe", "slaves", "nomasterpoll")
PROBE_KEYS = frozenset({"pings", "step", "offset", "packetsize", "timeout", "mininterval",
                        "hostinterval", "protocol"})  # fmt: skip
SECRET_KEYS = re.compile(r"pass|secret|community|token|key|urlformat|user|login", re.I)
_SECTION = re.compile(r"^\*\*\*\s*(\w+)\s*\*\*\*$")
_NODE = re.compile(r"^(\++)\s*(\S+)$")
_SETTING = re.compile(r"^([A-Za-z_][\w-]*)\s*=\s*(.*)$")


@dataclass
class _State:
    """Parser state across included files.

    Attributes:
        cfg: The result being built.
        section: Current ``*** section ***``, lower-cased.
        stack: Targets tree: one ``(name, settings)`` per level.
        probe_stack: Probes tree names per level.
        size: Bytes read so far.
    """

    cfg: SmokeConfig = field(default_factory=SmokeConfig)
    section: str = ""
    stack: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    probe_stack: list[str] = field(default_factory=list)
    size: int = 0


def _close_node(state: _State) -> None:
    """Record the innermost Targets node if it has a host, then pop it.

    Args:
        state: Parser state.

    Returns:
        None

    Raises:
        None
    """
    name, settings = state.stack.pop()
    host = settings.get("host", "")
    if not host or host.startswith("/"):
        return  # a section or a multi-host graph; neither has its own RRD
    inherited = dict(state.stack[0][1]) if state.stack else {}
    for _, level in state.stack[1:]:
        inherited.update({k: v for k, v in level.items() if k in INHERITED})
    inherited.update(settings)
    path = "/".join([*(n for n, _ in state.stack[1:]), name])
    slaves = tuple(inherited.get("slaves", "").split())
    master = inherited.get("nomasterpoll", "no").lower() != "yes"
    state.cfg.targets.append(SmokeTarget(path, host, inherited.get("probe", ""),
                                         settings.get("menu", ""), settings.get("title", ""),
                                         slaves, master))  # fmt: skip


def _node(state: _State, level: int, name: str) -> None:
    """Open a ``+``-level node in Targets or Probes.

    Args:
        state: Parser state.
        level: Number of ``+`` signs.
        name: Node name.

    Returns:
        None

    Raises:
        None
    """
    if state.section not in ("probes", "targets"):
        return  # Alerts, Presentation, and Slaves nodes describe nothing we keep
    if state.section == "probes":
        del state.probe_stack[level - 1 :]
        parent = state.probe_stack[-1] if state.probe_stack else ""
        state.probe_stack.append(name)
        state.cfg.probes[name] = {"_parent": parent} if parent else {}
        return
    while len(state.stack) > level:
        _close_node(state)
    state.stack.append((name, {}))


def _setting(state: _State, key: str, value: str) -> None:
    """Apply one ``key = value`` line in the current section.

    Args:
        state: Parser state.
        key: Setting name.
        value: Setting value.

    Returns:
        None

    Raises:
        None
    """
    if SECRET_KEYS.search(key) and len(value) >= 4:
        state.cfg.secrets.add(value)
    lower = key.lower()
    if state.section in ("general", "pathnames") and lower == "datadir":
        state.cfg.datadir = value
    elif state.section == "database" and lower in ("step", "pings") and value.isdigit():
        setattr(state.cfg, lower, int(value))
    elif state.section == "probes" and state.probe_stack and lower in PROBE_KEYS:
        state.cfg.probes[state.probe_stack[-1]][lower] = value
    elif state.section == "targets" and lower in TARGET_KEYS and state.stack:
        state.stack[-1][1][lower] = value


def _parse(state: _State, text: str, name: str, here: Path, depth: int) -> None:
    """Parse one file's content into the state.

    Args:
        state: Parser state.
        text: Content.
        name: File name for notes.
        here: Its directory, for relative includes.
        depth: Include depth.

    Returns:
        None

    Raises:
        None
    """
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@include"):
            target = Path(line.removeprefix("@include").strip())
            read_file(state, target if target.is_absolute() else here / target, depth + 1)
        elif match := _SECTION.match(line):
            while state.stack and state.section == "targets":
                _close_node(state)
            state.section, state.probe_stack = match[1].lower(), []
            state.stack = [("", {})] if state.section == "targets" else []
        elif match := _NODE.match(line):
            _node(state, len(match[1]), match[2])
        elif match := _SETTING.match(line):
            _setting(state, match[1], match[2].strip().strip('"'))
        else:
            state.cfg.notes.append(f"{name}:{number}: not a setting; ignored")


def read_file(state: _State, path: Path, depth: int = 0) -> None:
    """Read one configuration file, within the bounds.

    Args:
        state: Parser state.
        path: The file.
        depth: Include depth.

    Returns:
        None

    Raises:
        None
    """
    if depth > MAX_DEPTH or len(state.cfg.files) >= MAX_FILES:
        state.cfg.notes.append(f"{path}: include limit reached; not read")
        return
    try:
        data = path.read_bytes()
    except OSError as exc:
        state.cfg.notes.append(f"{path}: cannot read ({exc.strerror or type(exc).__name__})")
        return
    state.size += len(data)
    if state.size > MAX_BYTES:
        state.cfg.notes.append(f"{path}: configuration larger than {MAX_BYTES} bytes; not read")
        return
    state.cfg.files.append(str(path))
    _parse(state, data.decode("utf-8", errors="replace"), str(path), path.parent, depth)


def _finish(state: _State) -> SmokeConfig:
    """Close the open Targets nodes.

    Args:
        state: Parser state.

    Returns:
        SmokeConfig: The configuration.

    Raises:
        None
    """
    while state.stack and state.section == "targets":
        _close_node(state)
    return state.cfg


def parse_files(paths: list[Path]) -> SmokeConfig:
    """Read the configuration and its ``@include`` files.

    Args:
        paths: Top-level files.

    Returns:
        SmokeConfig: The configuration.

    Raises:
        None
    """
    state = _State()
    for path in paths:
        read_file(state, path)
    return _finish(state)


def parse_text(text: str, name: str) -> SmokeConfig:
    """Parse configuration text (tests).

    Args:
        text: Content.
        name: File name for notes.

    Returns:
        SmokeConfig: The configuration.

    Raises:
        None
    """
    state = _State()
    state.cfg.files.append(name)
    _parse(state, text, name, Path(), 0)
    return _finish(state)
