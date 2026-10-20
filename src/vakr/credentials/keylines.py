r"""Find the line of each TOML table header and key without looking at values.

``tomllib`` returns plain dictionaries with no positions, but an operator
fixing a missing field or a misspelled key wants a line number (CONF-009).
This scanner reads only what is left of ``=`` and inside ``[...]`` headers,
skips the bodies of multi-line strings, and returns integers. It is
best-effort: an unusual layout yields ``None``, never a wrong value.

Dependencies:
    re: Header and key patterns.

Safety:
    Nothing from the right-hand side of ``=`` is captured, and positions are
    only ever looked up with keys taken from the parsed document.

Example:
    >>> lines = KeyLines('[credentials.core]\nversion = "2c"\n')
    >>> lines.find(("credentials", "core")), lines.find(("credentials", "core", "version"))
    (1, 2)
"""

import re

_PART = r"""(?:"[^"\n]*"|'[^'\n]*'|[A-Za-z0-9_-]+)"""
_DOTTED = rf"{_PART}(?:\s*\.\s*{_PART})*"
_HEADER = re.compile(rf"\s*\[\[?\s*({_DOTTED})\s*\]\]?")
_KEY = re.compile(rf"\s*({_DOTTED})\s*=")
_SPLIT = re.compile(_PART)
_MULTILINE = re.compile(r'"""|' + r"'''")


def split_key(dotted: str) -> tuple[str, ...]:
    """Split a dotted TOML key into its parts, removing quotes.

    Args:
        dotted: Key text such as ``credentials."core-v2c"``.

    Returns:
        tuple[str, ...]: The parts, such as ``("credentials", "core-v2c")``.

    Raises:
        None
    """
    return tuple(part.strip("\"'") for part in _SPLIT.findall(dotted))


class KeyLines:
    """Line numbers of table headers and keys in one TOML document.

    Attributes:
        None public; use ``find``.
    """

    def __init__(self, text: str) -> None:
        """Scan the document once.

        Args:
            text: The TOML document.

        Returns:
            None

        Raises:
            None
        """
        self._lines: dict[tuple[str, ...], int] = {}
        table: tuple[str, ...] = ()
        in_string = False
        for number, line in enumerate(text.splitlines(), start=1):
            toggles = len(_MULTILINE.findall(line))
            if in_string:
                in_string = toggles % 2 == 0
                continue
            if header := _HEADER.match(line):
                table = split_key(header[1])
                self._lines.setdefault(table, number)
            elif key := _KEY.match(line):
                self._lines.setdefault((*table, *split_key(key[1])), number)
            in_string = toggles % 2 == 1

    def find(self, key: tuple[str, ...]) -> int | None:
        """Return the first line declaring ``key``.

        Args:
            key: Key parts, such as ``("credentials", "core", "community")``.

        Returns:
            int | None: 1-based line number, or ``None`` when not found.

        Raises:
            None
        """
        return self._lines.get(key)
