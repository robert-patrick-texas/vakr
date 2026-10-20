"""Operator-file layout, raw layers, and validation results.

These value types are shared by loading, upgrading, and annotating. Layers
keep each operator file's exact bytes, which is how a failed upgrade proves
that files are byte-identical afterwards (CFGM-003), and results record where
every effective value came from so an operator can answer "why is this set?"
during an outage (CONF-006).

Dependencies:
    dataclasses, pathlib: Plain immutable records.

Safety:
    ``read_layers`` only reads; nothing here writes files.

Example:
    >>> str(Issue(Origin("/etc/vakr/config.toml", 7), "update.chanel", "unknown key"))
    '/etc/vakr/config.toml:7: update.chanel: unknown key'
"""

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION_KEY = "config_version"
"""Reserved top-level key recording an operator file's schema version."""


@dataclass(frozen=True, slots=True)
class ConfigLayout:
    """Where operator files live.

    Attributes:
        config_dir: Operator configuration directory.
        main_file: Base file name inside ``config_dir``.
        dropin_dir: Drop-in directory name inside ``config_dir``.
    """

    config_dir: Path
    main_file: str = "config.toml"
    dropin_dir: str = "conf.d"

    @property
    def main_path(self) -> Path:
        """Path of the main operator file.

        Args:
            None

        Returns:
            Path: ``config_dir / main_file``.

        Raises:
            None
        """
        return self.config_dir / self.main_file

    def files(self) -> list[Path]:
        """List existing operator files in precedence order (lowest first).

        Args:
            None

        Returns:
            list[Path]: The main file, then drop-ins sorted by name.

        Raises:
            None
        """
        dropins = sorted((self.config_dir / self.dropin_dir).glob("*.toml"))
        return [path for path in (self.main_path, *dropins) if path.is_file()]


@dataclass(frozen=True, slots=True)
class Layer:
    """The raw bytes of one operator file, read once and reused.

    Attributes:
        path: File path.
        raw: Exact file content.
    """

    path: Path
    raw: bytes


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a value or problem comes from.

    Attributes:
        path: File path, or a label such as ``"default"`` or ``"override"``.
        line: 1-based line, when known.
    """

    path: str
    line: int | None = None

    def __str__(self) -> str:
        """Render as ``path:line`` or ``path``.

        Args:
            None

        Returns:
            str: Location text.

        Raises:
            None
        """
        return f"{self.path}:{self.line}" if self.line else self.path


DEFAULT_ORIGIN = Origin("default")
"""Origin of values that come from the catalog."""


@dataclass(frozen=True, slots=True)
class Issue:
    """One validation problem, formatted ``file:line: key: message``.

    Attributes:
        origin: File and line.
        key: Dotted key, or ``""`` for whole-file problems.
        message: What is wrong and, where possible, how to fix it.
    """

    origin: Origin
    key: str
    message: str

    def __str__(self) -> str:
        """Render the problem for operators.

        Args:
            None

        Returns:
            str: ``file:line: key: message`` (no key for whole-file problems).

        Raises:
            None
        """
        where = f"{self.origin}: {self.key}" if self.key else str(self.origin)
        return f"{where}: {self.message}"


@dataclass(slots=True)
class LoadResult:
    """Effective configuration plus its provenance and problems.

    Attributes:
        values: Effective value of every catalog option, by dotted key, in
            catalog order.
        sources: Origins of each value; the last one wins, and ``append``
            options list every contributor.
        issues: Validation problems; empty means the configuration is valid.
        versions: ``config_version`` of each operator file, by path.
    """

    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, tuple[Origin, ...]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    versions: dict[str, int] = field(default_factory=dict)

    def nested(self) -> dict[str, Any]:
        """Return the effective values as nested tables.

        Args:
            None

        Returns:
            dict[str, Any]: ``{"update": {"channel": ...}, ...}``, copied.

        Raises:
            None
        """
        root: dict[str, Any] = {}
        for key, value in self.values.items():
            *tables, leaf = key.split(".")
            node = root
            for table in tables:
                node = node.setdefault(table, {})
            node[leaf] = copy.deepcopy(value)
        return root

    def operator_keys(self) -> set[str]:
        """Return the keys some operator file or override sets explicitly.

        Args:
            None

        Returns:
            set[str]: Dotted keys whose sources include a non-default origin.

        Raises:
            None
        """
        return {k for k, origins in self.sources.items() if set(origins) - {DEFAULT_ORIGIN}}


def read_layers(layout: ConfigLayout) -> list[Layer]:
    """Read every operator file once, in precedence order.

    Args:
        layout: Where the files live.

    Returns:
        list[Layer]: One layer per existing file.

    Raises:
        OSError: If a file exists but cannot be read.
    """
    return [Layer(path, path.read_bytes()) for path in layout.files()]
