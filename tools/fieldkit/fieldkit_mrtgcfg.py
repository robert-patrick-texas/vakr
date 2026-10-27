# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Read MRTG configuration: targets, their limits and options, and where data lives.

The ``mrtg`` profile compares MRTG's numbers with vakr's, so it needs every
``Target[]`` with the settings that change what MRTG records: ``MaxBytes``
(and ``MaxBytes1``/``MaxBytes2``, ``AbsMax``), ``Options`` (``gauge``,
``absolute``, ``unknaszero``, ...), ``Directory``, and the global
``WorkDir``, ``LogDir``, ``LogFormat``, and ``Interval``. MRTG's file format
has continuation lines (a line starting with white space), ``Include:``
with wildcards, and the special targets ``_`` (default), ``^`` (prepend),
and ``$`` (append). Only those keywords are kept: titles, page text,
``SnmpOptions`` (SNMPv3 passwords), and ``SetEnv`` are ignored, and no raw
configuration text ever leaves this module.

Dependencies:
    fieldkit_mrtgmodel, fieldkit_mrtgtarget: Records and ``Target[]`` parsing.
    re: Keyword lines.

Safety:
    Reads at most ``MAX_FILES`` files and ``MAX_BYTES`` bytes, follows
    includes at most ``MAX_DEPTH`` deep, and never writes. Communities are
    collected in ``secrets`` (hidden from ``repr``) only so the bundle
    writer can redact them; errors name files and line numbers, never text.

Example:
    >>> cfg = parse_text("WorkDir: /var/www/mrtg\nTarget[r1_2]: 2:pw@r1\n", "mrtg.cfg")
    >>> [(t.name, t.spec.kind, t.spec.key) for t in cfg.targets], cfg.globals["workdir"]
    ([('r1_2', 'ifindex', '2')], '/var/www/mrtg')
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from fieldkit_mrtgmodel import MrtgConfig, MrtgTarget, expand
from fieldkit_mrtgtarget import parse_target

MAX_FILES = 200
MAX_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 8
TARGET_KEYS = frozenset({"target", "maxbytes", "maxbytes1", "maxbytes2", "absmax", "options",
                         "directory"})  # fmt: skip
GLOBAL_KEYS = frozenset({"workdir", "logdir", "logformat", "interval", "enableipv6",
                         "runasdaemon", "include"})  # fmt: skip
_TARGET_LINE = re.compile(r"^([A-Za-z0-9]+)\[([^\]]+)\]\s*:\s?(.*)$")
_GLOBAL_LINE = re.compile(r"^([A-Za-z0-9]+)\s*:\s?(.*)$")


@dataclass
class _State:
    """Parser state across included files.

    Attributes:
        cfg: The result being built.
        defaults: ``[_]`` values; ``prepend`` and ``append`` are ``[^]`` and ``[$]``.
        prepend: ``[^]`` values.
        append: ``[$]`` values.
        seen: Target name to its values, in first-seen order.
        sources: Target name to the ``file:line`` of its ``Target[]``.
        size: Bytes read so far.
    """

    cfg: MrtgConfig = field(default_factory=MrtgConfig)
    defaults: dict[str, str] = field(default_factory=dict)
    prepend: dict[str, str] = field(default_factory=dict)
    append: dict[str, str] = field(default_factory=dict)
    seen: dict[str, dict[str, str]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    size: int = 0


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Join continuation lines and drop comments and blank lines.

    Args:
        text: File content.

    Returns:
        list[tuple[int, str]]: ``(first line number, joined line)``.

    Raises:
        None
    """
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace() and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + line.strip())
        else:
            out.append((number, line))
    return out


def _keyword(state: _State, key: str, name: str, value: str, where: str) -> None:
    """Apply one ``Keyword[name]: value`` line.

    Args:
        state: Parser state.
        key: Lower-cased keyword.
        name: Target name or ``_``, ``^``, ``$``.
        value: The value.
        where: ``file:line``.

    Returns:
        None

    Raises:
        None
    """
    special = {"_": state.defaults, "^": state.prepend, "$": state.append}
    if name in special:
        special[name][key] = value
        return
    values = state.seen.setdefault(name, dict(state.defaults))
    if key == "options":
        value = " ".join(v for v in (state.prepend.get(key), value, state.append.get(key)) if v)
    values[key] = value
    if key == "target":
        state.sources.setdefault(name, where)


def _include(state: _State, value: str, here: Path, depth: int) -> None:
    """Follow ``Include:``, relative to the including file, with ``*`` wildcards.

    Args:
        state: Parser state.
        value: The include path.
        here: Directory of the including file.
        depth: Current include depth.

    Returns:
        None

    Raises:
        None
    """
    pattern = value.strip()
    pattern = pattern if Path(pattern).is_absolute() else str(here / pattern)
    for path in expand(pattern):
        read_file(state, path, depth + 1)


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
    for number, line in logical_lines(text):
        where = f"{name}:{number}"
        if match := _TARGET_LINE.match(line):
            key = match[1].lower()
            if key in TARGET_KEYS:
                _keyword(state, key, match[2], match[3].strip(), where)
        elif match := _GLOBAL_LINE.match(line):
            key = match[1].lower()
            if key == "include":
                _include(state, match[2], here, depth)
            elif key in GLOBAL_KEYS:
                state.cfg.globals[key] = match[2].strip()
        else:
            state.cfg.notes.append(f"{where}: not a keyword line; ignored")


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


def _finish(state: _State) -> MrtgConfig:
    """Turn the collected keyword values into targets.

    Args:
        state: Parser state.

    Returns:
        MrtgConfig: The configuration.

    Raises:
        None
    """
    cfg = state.cfg
    for name, values in state.seen.items():
        if "target" not in values:
            continue
        spec = parse_target(values["target"])
        cfg.secrets.update(c for c in spec.communities if c)
        kept = {k: v for k, v in values.items() if k != "target"}
        cfg.targets.append(MrtgTarget(name, spec, kept, state.sources.get(name, "")))
    return cfg


def parse_files(paths: list[Path]) -> MrtgConfig:
    """Read configuration files and their includes.

    Args:
        paths: Top-level files.

    Returns:
        MrtgConfig: Targets, globals, notes, and secrets.

    Raises:
        None
    """
    state = _State()
    for path in paths:
        read_file(state, path)
    return _finish(state)


def parse_text(text: str, name: str) -> MrtgConfig:
    """Parse configuration text (tests and single files).

    Args:
        text: Content.
        name: File name for notes.

    Returns:
        MrtgConfig: The configuration.

    Raises:
        None
    """
    state = _State()
    state.cfg.files.append(name)
    _parse(state, text, name, Path(), 0)
    return _finish(state)
