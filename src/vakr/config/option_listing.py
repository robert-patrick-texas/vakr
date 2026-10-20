"""Listings for ``vakr config new-options``: short by default, full with ``--describe`` (CFGM-004).

Self-update prints this listing after an update, so its default is short:
one line per section naming the new options, which a release with dozens of
new options keeps to about a dozen lines. Every default applies without an
edit, so the names are enough to know what arrived; ``--describe`` adds each
option's type, reload behaviour, and description. The header line
``options added since X: N`` is unchanged in both forms, because updaters
from earlier releases parse it.

Dependencies:
    textwrap: Wrapping for terminals.
    vakr.lifecycle.configmerge: Option metadata and TOML rendering.

Safety:
    Pure; option defaults are never secrets.

Example:
    >>> brief_lines([], "0.1.1")
    []
"""

import textwrap
from collections.abc import Sequence
from itertools import groupby

from vakr.lifecycle.configmerge import Option, toml_literal
from vakr.lifecycle.configmerge.render import type_label

WIDTH = 100
"""Wrap listings to fit a terminal with room for the update log's prefixes."""


def brief_lines(options: Sequence[Option], since: str) -> list[str]:
    """List new options by section, names only, and how to see details.

    Args:
        options: The new options, in catalog order.
        since: The baseline release, for the details hint.

    Returns:
        list[str]: One wrapped line per section, then the hint; empty when
        nothing is new.

    Raises:
        None
    """
    if not options:
        return []
    lines: list[str] = []
    ordered = sorted(options, key=lambda o: o.key.split(".", 1)[0])
    for section, group in groupby(ordered, key=lambda o: o.key.split(".", 1)[0]):
        names = [o.key.split(".", 1)[1] if "." in o.key else o.key for o in group]
        text = f"{section} ({len(names)}): {', '.join(names)}"
        lines.extend(textwrap.wrap(text, WIDTH, initial_indent="  ", subsequent_indent="      "))
    lines.append(f"  descriptions: vakr config new-options --since {since} --describe")
    return lines


def described_lines(options: Sequence[Option]) -> list[str]:
    """List new options with their defaults, types, reload behaviour, and descriptions.

    Args:
        options: The new options, in catalog order.

    Returns:
        list[str]: Two or more lines per option.

    Raises:
        None
    """
    lines: list[str] = []
    for option in options:
        lines.append(f"  {option.key} = {toml_literal(option.default)}")
        meta = f"{type_label(option.spec)}; reload {option.reload}; since {option.since}"
        text = textwrap.fill(f"{meta}. {option.description}", 90)
        lines.append(textwrap.indent(text, " " * 6))
    return lines
