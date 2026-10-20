"""Option catalog: the single, data-driven description of every setting.

A long-lived monitoring deployment collects configuration over years of
upgrades. Keeping every option's key, type, default, release of introduction,
and reload class in one data file (not scattered through code) lets one tool
validate operator files, render the packaged defaults and documentation, and
tell operators exactly which options a new release added (CFGM-001,
CFGM-004).

This module holds the in-memory model; ``catalog_file`` parses and
self-checks catalog files.

Dependencies:
    values: Value specifications.

Safety:
    Immutable after construction; defaults are deep-copied on the way out so
    callers cannot alter the catalog.

Example:
    >>> spec = ValueSpec("enum", choices=("stable", "prerelease"))
    >>> option = Option("update.channel", spec, "stable", "1.0.0", "live", "Channel.")
    >>> Catalog(1, [option]).is_table("update"), option.name
    (True, 'channel')
    >>> version_key("1.2.0-rc.1") < version_key("1.2.0")
    True
"""

import copy
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from .values import ValueSpec

RELOAD_CLASSES = ("live", "restart")
"""``live`` options apply on reload; ``restart`` options need a service restart."""

MERGE_MODES = ("replace", "append")
"""How successive layers combine: later replaces earlier, or arrays concatenate."""

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-?rc\.?(\d+))?(?:\+[\w.]+)?$")


class CatalogError(ValueError):
    """The option catalog is malformed; this is a packaging bug, not operator error."""


@dataclass(frozen=True, slots=True)
class Option:
    """One configuration option.

    Attributes:
        key: Dotted key, such as ``"update.channel"`` for ``[update] channel``.
        spec: Type and constraints.
        default: Packaged default; always valid under ``spec``.
        since: Release that introduced the option (SemVer).
        reload: One of ``RELOAD_CLASSES``.
        description: Operator-facing explanation.
        merge: One of ``MERGE_MODES``; ``append`` concatenates arrays across
            the default and operator layers.
        defaults_switch: Key of a ``bool`` option; when it is ``false`` the
            packaged entries of an ``append`` option are dropped.
        required_when: ``(key, value)``: this option must be non-empty when
            that other option has that value.
    """

    key: str
    spec: ValueSpec
    default: Any
    since: str
    reload: str
    description: str
    merge: str = "replace"
    defaults_switch: str = ""
    required_when: tuple[str, Any] | None = None

    @property
    def table(self) -> str:
        """Dotted name of the table holding the option (``""`` for top level).

        Args:
            None

        Returns:
            str: For ``"update.channel"``, ``"update"``.

        Raises:
            None
        """
        return self.key.rpartition(".")[0]

    @property
    def name(self) -> str:
        """Key of the option within its table.

        Args:
            None

        Returns:
            str: For ``"update.channel"``, ``"channel"``.

        Raises:
            None
        """
        return self.key.rpartition(".")[2]


class Catalog:
    """An ordered, indexed set of options plus the current schema version.

    Immutable after construction and safe to share between threads.
    """

    def __init__(self, config_version: int, options: Sequence[Option]) -> None:
        """Index ``options`` by key and by enclosing table.

        Args:
            config_version: Schema version operator files are upgraded to.
            options: Options in documentation order.

        Returns:
            None

        Raises:
            None
        """
        self.config_version = config_version
        self.options = tuple(options)
        self._index = {o.key: o for o in self.options}
        self._tables = {t for o in self.options for t in table_prefixes(o.key)}

    def option(self, key: str) -> Option | None:
        """Return the option with dotted ``key``, if the catalog defines it.

        Args:
            key: Dotted option key.

        Returns:
            Option | None: The option, or ``None``.

        Raises:
            None
        """
        return self._index.get(key)

    def is_table(self, key: str) -> bool:
        """Report whether ``key`` names a table that contains options.

        Args:
            key: Dotted table name, such as ``"update"``.

        Returns:
            bool: ``True`` when some option lives under ``key``.

        Raises:
            None
        """
        return key in self._tables

    def names(self) -> list[str]:
        """Return every option key and table name, for closest-match suggestions.

        Args:
            None

        Returns:
            list[str]: Option keys followed by table names.

        Raises:
            None
        """
        return [*self._index, *sorted(self._tables)]

    def defaults(self) -> dict[str, Any]:
        """Return the packaged default of every option, keyed by dotted key.

        Args:
            None

        Returns:
            dict[str, Any]: Independent copies of the defaults.

        Raises:
            None
        """
        return {o.key: copy.deepcopy(o.default) for o in self.options}


def table_prefixes(key: str) -> Iterator[str]:
    """Yield the dotted tables enclosing ``key``, outermost first.

    Args:
        key: Dotted key such as ``"a.b.c"``.

    Returns:
        Iterator[str]: ``"a"`` then ``"a.b"``.

    Raises:
        None
    """
    parts = key.split(".")
    for end in range(1, len(parts)):
        yield ".".join(parts[:end])


def version_key(text: object) -> tuple[int, int, int, int, int]:
    """Turn a release version into a sortable key; finals sort after their RCs.

    Accepts SemVer (``1.2.0-rc.1``), PEP 440 (``1.2.0rc1``), a leading ``v``,
    and a ``+local`` suffix, because versions arrive from catalogs, package
    metadata, and operators.

    Args:
        text: Version string.

    Returns:
        tuple[int, int, int, int, int]: Major, minor, patch, final flag, RC.

    Raises:
        ValueError: If ``text`` is not a recognisable release version.
    """
    match = _VERSION.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f"not a release version: {text!r}")
    major, minor, patch, rc = match.groups()
    return int(major), int(minor), int(patch), 0 if rc else 1, int(rc or 0)
