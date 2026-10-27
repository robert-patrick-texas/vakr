# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Parse MRTG ``.log`` files and recorded ``rrdtool info``/``fetch`` output into windows.

The field kit stores MRTG's and SmokePing's data exactly as those tools
write or print it, so this module reads the native formats:

* **MRTG ``.log``** (rateup): a counter line ``time in out``, then rows
  ``time avg_in avg_out max_in max_out`` in bytes per second, newest
  first. Row *i* covers the time since row *i+1*; the first row is the
  partial interval since the last aligned row, and after about 50 hours
  the rows are 30-minute ones. Only rows exactly one step long are windows.
  Unknown polls are not marked: MRTG repeats the last value (or logs 0
  with ``unknaszero``).
* **``rrdtool fetch``**: a header of data-source names, a blank line, and
  ``time: v1 v2 ...`` rows, oldest first, each covering the step ending at
  its time. ``nan`` and ``-nan`` are unknown. MRTG's sources ``ds0``/``ds1``
  are in/out bytes per second; SmokePing's are ``uptime``, ``loss`` (lost
  pings per round), ``median`` (seconds), and ``ping1`` .. ``pingN``.
* **``rrdtool info``**: ``key = value`` lines (the kit keeps ``step``,
  ``last_update``, and the data-source and archive settings).

Dependencies:
    math, dataclasses: Standard library only.

Safety:
    Pure parsing; malformed lines are skipped and counted, never fatal.

Example:
    >>> rows = parse_log("1792497903 9 9\n1792497903 5 6 7 8\n1792497900 10 20 30 40\n"
    ...                  "1792497600 1 2 3 4\n", 300)
    >>> [(w.end, w.values) for w in rows.windows]
    [(1792497900, (80.0, 160.0))]
    >>> rows.last_run
    1792497903
"""

import itertools
import math
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Window:
    """One window of another tool's data.

    Attributes:
        end: Window end, epoch seconds.
        step: Window length, seconds.
        values: Values by position (MRTG: in and out bits/s; SmokePing: by source).
    """

    end: int
    step: int
    values: tuple[float | None, ...]


@dataclass
class Parsed:
    """The windows of one file.

    Attributes:
        windows: Windows exactly one step long, oldest first.
        names: Data-source names (rrdtool) or ``("in", "out")``.
        last_run: When the tool last updated the file, if known.
        skipped: Lines that did not parse.
    """

    windows: list[Window] = field(default_factory=list)
    names: tuple[str, ...] = ()
    last_run: int | None = None
    skipped: int = 0


def number(text: str) -> float | None:
    """Parse a number; ``nan``, ``-nan``, ``U``, and empty text are unknown.

    Args:
        text: Cell text.

    Returns:
        float | None: The value, or ``None``.

    Raises:
        None
    """
    try:
        value = float(text)
    except ValueError:
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def parse_log(text: str, step: int) -> Parsed:
    """Parse an MRTG rateup ``.log`` into bit-per-second windows.

    Args:
        text: The log, as the field kit stored it.
        step: The window length to keep (300).

    Returns:
        Parsed: Windows of ``(in_bps, out_bps)``, oldest first.

    Raises:
        None
    """
    out = Parsed(names=("in", "out"))
    rows: list[tuple[int, float, float]] = []
    for index, line in enumerate(text.splitlines()):
        parts = line.split()
        if index == 0:
            out.last_run = int(parts[0]) if parts and parts[0].isdigit() else None
            continue
        if len(parts) < 3 or not parts[0].isdigit():
            out.skipped += 1
            continue
        try:
            rows.append((int(parts[0]), float(parts[1]) * 8, float(parts[2]) * 8))
        except ValueError:
            out.skipped += 1
    for newer, older in itertools.pairwise(rows):
        if newer[0] - older[0] == step:
            out.windows.append(Window(newer[0], step, (newer[1], newer[2])))
    out.windows.reverse()
    return out


def parse_info(text: str) -> dict[str, str]:
    """Parse kept ``rrdtool info`` lines.

    Args:
        text: ``key = value`` lines.

    Returns:
        dict[str, str]: Keys to values without quotes.

    Raises:
        None
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(" = ")
        if sep:
            found[key.strip()] = value.strip().strip('"')
    return found


def parse_fetch(text: str, step: int, scale: float = 1.0) -> Parsed:
    """Parse ``rrdtool fetch`` output, keeping rows exactly ``step`` apart.

    Args:
        text: The recorded output.
        step: The resolution to keep.
        scale: Multiplier for every value (8 turns MRTG's bytes into bits).

    Returns:
        Parsed: Windows of the sources' values, oldest first.

    Raises:
        None
    """
    out = Parsed()
    rows: list[tuple[int, tuple[float | None, ...]]] = []
    for line in text.splitlines():
        stamp, sep, rest = line.partition(":")
        if not sep:
            if line.strip() and not out.names:
                out.names = tuple(line.split())
            continue
        if not stamp.strip().isdigit():
            out.skipped += 1
            continue
        values = tuple(None if (v := number(c)) is None else v * scale for c in rest.split())
        if len(values) != len(out.names):
            out.skipped += 1
            continue
        rows.append((int(stamp), values))
    for i, (end, values) in enumerate(rows):
        neighbour = rows[i - 1][0] if i else (2 * end - rows[1][0] if len(rows) > 1 else None)
        if neighbour is not None and end - neighbour == step:
            out.windows.append(Window(end, step, values))
    return out


def mrtg_windows(fetch: str, info: str, step: int) -> Parsed:
    """Parse an MRTG RRD's fetch output into bit-per-second windows.

    Args:
        fetch: ``rrdtool fetch`` output (``ds0`` in, ``ds1`` out, bytes/s).
        info: Kept ``rrdtool info`` lines.
        step: The window length to keep.

    Returns:
        Parsed: Windows of ``(in_bps, out_bps)``, with ``last_run`` from ``last_update``.

    Raises:
        None
    """
    parsed = parse_fetch(fetch, step, scale=8.0)
    found = parse_info(info)
    last = found.get("last_update", "")
    parsed.last_run = int(float(last)) if last.replace(".", "", 1).isdigit() else None
    indexes = [parsed.names.index(n) for n in ("ds0", "ds1") if n in parsed.names]
    if len(indexes) == 2:
        parsed.windows = [Window(w.end, w.step, (w.values[indexes[0]], w.values[indexes[1]]))
                          for w in parsed.windows]  # fmt: skip
    parsed.names = ("in", "out")
    return parsed


@dataclass(frozen=True, slots=True)
class Round:
    """One SmokePing row: a round of pings, as the RRD consolidated it.

    Attributes:
        end: Row end, epoch seconds.
        step: Row length.
        median_ms: Median RTT, or ``None`` when every ping was lost or unknown.
        loss: Lost pings (fractional where rounds were blended), or ``None``.
        pings: Pings per round.
        rtts_ms: The known ``pingN`` values, sorted.
    """

    end: int
    step: int
    median_ms: float | None
    loss: float | None
    pings: int
    rtts_ms: tuple[float, ...]


def smokeping_rounds(fetch: str, step: int) -> list[Round]:
    """Parse a SmokePing RRD's fetch output into rounds.

    Args:
        fetch: ``rrdtool fetch`` output.
        step: The step to keep.

    Returns:
        list[Round]: Rounds, oldest first; rows without a ``median`` or
        ``loss`` source are skipped.

    Raises:
        None
    """
    parsed = parse_fetch(fetch, step)
    names = parsed.names
    if "median" not in names or "loss" not in names:
        return []
    median, loss = names.index("median"), names.index("loss")
    pings = [i for i, n in enumerate(names) if n.startswith("ping")]
    rounds = []
    for w in parsed.windows:
        rtts = sorted(v * 1000 for i in pings if (v := w.values[i]) is not None)
        med = w.values[median]
        rounds.append(Round(w.end, w.step, None if med is None else med * 1000, w.values[loss],
                            len(pings), tuple(rtts)))  # fmt: skip
    return rounds
