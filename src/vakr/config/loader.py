"""Load the effective configuration: packaged defaults merged with operator files.

This is the stable interface every component uses to read settings. The
packaged option catalog supplies a documented default for every option
(CFGM-001); ``/etc/vakr/config.toml`` and lexically ordered
``conf.d/*.toml`` files override it key by key (CONF-006). The merged result
is validated, so a component never runs on a typo, and each value remembers
the file and line that set it.

Dependencies:
    vakr.config.project: vakr's catalog, migrations, and file layout.

Safety:
    Reading configuration never executes code or follows includes outside
    the configuration directory. Error messages name files, lines, and keys,
    never values.

Example:
    >>> cfg = EffectiveConfig({"update": {"channel": "prerelease"}})
    >>> cfg.get("update.channel"), cfg.get("update.hold_version")
    ('prerelease', None)
"""

import copy
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from vakr.config import project
from vakr.lifecycle.configmerge import Issue, Origin

_MISSING = object()


class ConfigError(ValueError):
    """The operator configuration is invalid; nothing should start on it.

    Subclasses ``ValueError`` so callers that already caught TOML syntax
    errors (``tomllib.TOMLDecodeError`` is a ``ValueError``) keep working.

    Attributes:
        issues: Every problem, formatted ``file:line: key: message``.
    """

    def __init__(self, issues: Sequence[Issue]) -> None:
        """Record the validation problems.

        Args:
            issues: Problems found while loading.

        Returns:
            None

        Raises:
            None
        """
        self.issues = tuple(issues)
        lines = "\n".join(str(issue) for issue in self.issues)
        super().__init__(f"invalid configuration ({len(self.issues)} problem(s)):\n{lines}")


class EffectiveConfig:
    """Read-only view of merged configuration with dotted-key lookup.

    Instances are immutable after construction; callers receive copies of
    tables and lists, never references to internal state. Safe to share
    between threads for reads.
    """

    def __init__(
        self, data: Mapping[str, Any], sources: Mapping[str, tuple[Origin, ...]] | None = None
    ) -> None:
        """Wrap already-merged configuration data.

        Args:
            data: Nested tables as produced by merging TOML files.
            sources: Origins of each dotted option key, when known.

        Returns:
            None

        Raises:
            None
        """
        self._data: Mapping[str, Any] = data
        self._sources = dict(sources or {})

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Return the value at ``dotted_key`` such as ``"update.channel"``.

        Args:
            dotted_key: Table path and key joined by dots.
            default: Value returned when any part of the path is absent.

        Returns:
            Any: The configured value (a copy for tables and lists), or
            ``default``.

        Raises:
            None
        """
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, Mapping):
                return default
            node = node.get(part, _MISSING)
            if node is _MISSING:
                return default
        return copy.deepcopy(node) if isinstance(node, Mapping | list) else node

    def source(self, dotted_key: str) -> str:
        """Describe where an option's effective value came from.

        Args:
            dotted_key: Option key such as ``"update.channel"``.

        Returns:
            str: ``"default"``, ``"/etc/vakr/config.toml:12"``, or several
            comma-separated origins for options that merge layers; ``""``
            when unknown.

        Raises:
            None
        """
        return ", ".join(str(origin) for origin in self._sources.get(dotted_key, ()))

    def as_dict(self) -> dict[str, Any]:
        """Return a deep copy of the merged configuration as plain dicts.

        Args:
            None

        Returns:
            dict[str, Any]: Copy safe for the caller to modify.

        Raises:
            None
        """
        return copy.deepcopy(dict(self._data))


def load_effective_config(config_dir: Path) -> EffectiveConfig:
    """Merge packaged defaults, ``config.toml``, and ``conf.d/*.toml`` from ``config_dir``.

    Later files override earlier ones key by key; tables merge recursively.
    Missing files are treated as empty, so a fresh install runs on defaults.
    Files with an older ``config_version`` are migrated in memory until
    ``vakr config upgrade`` rewrites them.

    Args:
        config_dir: Operator configuration directory, normally ``/etc/vakr``.

    Returns:
        EffectiveConfig: The merged, validated view.

    Raises:
        ConfigError: If any file is not valid TOML, or the merged
            configuration has unknown keys or invalid values.
        OSError: If a present file cannot be read.
    """
    result = project.inspect(config_dir)
    if result.issues:
        raise ConfigError(result.issues)
    return EffectiveConfig(result.nested(), result.sources)
