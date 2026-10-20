r"""Find the line on which each key of a TOML file is set.

Validation errors are only actionable when they say where to look:
``/etc/vakr/conf.d/50-site.toml:12: update.chanel: unknown key`` sends the
operator straight to the typo (CFGM-006). TOML parsers do not report key
positions, so this lightweight scanner recovers them from the text. It
understands table and array-of-table headers, dotted and quoted keys,
multi-line arrays, and multi-line strings; anything it cannot place falls
back to the enclosing table's line, or to no line at all.

Dependencies:
    re: Header and key recognition.

Safety:
    Read-only, linear in the file size, and never raises on odd input,
    because it only improves messages and must not break validation.

Example:
    >>> key_lines('[update]\nchannel = "stable"\n')["update.channel"]
    2
    >>> line_of({"a": 1}, "a.b[0].c")
    1
"""

import re
from collections.abc import Mapping

_HEADER = re.compile(r"^\s*(\[\[?)\s*([^\[\]]+?)\s*\]\]?\s*(?:#.*)?$")
_KEY = re.compile(
    r"""^\s*((?:[\w-]+|"[^"]*"|'[^']*')(?:\s*\.\s*(?:[\w-]+|"[^"]*"|'[^']*'))*)\s*="""
)
_PART = re.compile(r"""[\w-]+|"([^"]*)"|'([^']*)'""")
_STRINGS = re.compile(r""""(?:[^"\\]|\\.)*"|'[^']*'""")


def _dotted(raw: str) -> str:
    """Normalise a TOML key such as ``a . "b.c"`` to ``a.b.c``.

    Args:
        raw: Key text from a header or assignment.

    Returns:
        str: Dotted key with quotes and spaces removed.

    Raises:
        None
    """
    parts = []
    for match in _PART.finditer(raw):
        quoted = match.group(1) if match.group(1) is not None else match.group(2)
        parts.append(quoted if quoted is not None else match.group(0))
    return ".".join(parts)


def _depth_change(text: str) -> int:
    """Count bracket nesting opened (positive) or closed by a value fragment.

    Args:
        text: Part of a line after ``=``, or a continuation line.

    Returns:
        int: Opened minus closed ``[``/``{`` outside strings and comments.

    Raises:
        None
    """
    code = _STRINGS.sub("", text).split("#", 1)[0]
    return code.count("[") + code.count("{") - code.count("]") - code.count("}")


def _opens_multiline_string(text: str) -> str | None:
    """Return the delimiter of a multi-line string left open by ``text``.

    Args:
        text: Value text after ``=``.

    Returns:
        str | None: ``'\"\"\"'`` or ``"'''"`` when one is left open.

    Raises:
        None
    """
    for delimiter in ('"""', "'''"):
        if text.count(delimiter) % 2 == 1:
            return delimiter
    return None


def scan(text: str) -> tuple[dict[str, int], list[tuple[int, str, str]]]:
    """Scan TOML text for key and header lines.

    Args:
        text: TOML document.

    Returns:
        tuple[dict[str, int], list[tuple[int, str, str]]]: First, 1-based
        line of each dotted key (array elements appear as ``name[i]``);
        second, ``(line, bracket, name)`` for each header, where bracket is
        ``"["`` or ``"[["``.

    Raises:
        None
    """
    lines: dict[str, int] = {}
    headers: list[tuple[int, str, str]] = []
    counters: dict[str, int] = {}
    prefix, depth, open_string = "", 0, None
    for number, raw in enumerate(text.splitlines(), 1):
        if open_string is not None:
            open_string = None if raw.count(open_string) % 2 == 1 else open_string
        elif depth > 0:
            depth = max(0, depth + _depth_change(raw))
        elif header := _HEADER.match(raw):
            name = _dotted(header.group(2))
            headers.append((number, header.group(1), name))
            lines.setdefault(name, number)
            if header.group(1) == "[[":
                counters[name] = counters.get(name, -1) + 1
                name = f"{name}[{counters[name]}]"
                lines.setdefault(name, number)
            prefix = f"{name}."
        elif key := _KEY.match(raw):
            lines.setdefault(prefix + _dotted(key.group(1)), number)
            rest = raw[key.end() :]
            depth, open_string = max(0, _depth_change(rest)), _opens_multiline_string(rest)
    return lines, headers


def key_lines(text: str) -> dict[str, int]:
    """Return the 1-based line on which each dotted key is first set.

    Args:
        text: TOML document.

    Returns:
        dict[str, int]: Dotted key (``name[i]`` for array elements) to line.

    Raises:
        None
    """
    return scan(text)[0]


def line_of(lines: Mapping[str, int], key: str) -> int | None:
    """Find the line for ``key``, falling back to its closest enclosing table.

    Args:
        lines: Result of ``key_lines``.
        key: Dotted key, possibly with ``[i]`` element indexes.

    Returns:
        int | None: Line number, or ``None`` when nothing encloses the key.

    Raises:
        None
    """
    probe = key
    while probe:
        if probe in lines:
            return lines[probe]
        cut = max(probe.rfind("."), probe.rfind("["))
        probe = probe[:cut] if cut > 0 else ""
    return None


def table_insert_index(text: str, table: str) -> int | None:
    """Return where new lines belong at the end of ``[table]``'s own section.

    The index is just after the section's last setting, before trailing blank
    lines and before any comment block that introduces the next header.

    Args:
        text: TOML document.
        table: Dotted table name; ``""`` means the top-level section.

    Returns:
        int | None: 0-based line index for insertion, or ``None`` when the
        file has no ``[table]`` header.

    Raises:
        None
    """
    rows = text.splitlines()
    _, headers = scan(text)
    starts = [n - 1 for n, bracket, name in headers if bracket == "[" and name == table]
    if table and not starts:
        return None
    start = starts[0] if table else -1
    end = next((n - 1 for n, _, _ in headers if n - 1 > start), len(rows))
    # Comments glued to the next header describe that table; at end of file
    # there is no next header, so trailing comments belong to this one.
    while end < len(rows) and end > start + 1 and rows[end - 1].lstrip().startswith("#"):
        end -= 1
    while end > start + 1 and not rows[end - 1].strip():
        end -= 1
    return end
