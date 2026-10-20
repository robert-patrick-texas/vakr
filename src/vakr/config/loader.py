"""Load the effective configuration from operator TOML files.

This is the stable interface every component uses to read settings. The
first implementation reads ``config.toml`` and lexically ordered
``conf.d/*.toml`` files only; the full layering (packaged option catalog,
environment and CLI overrides, per-key source tracking, validation) replaces
the internals without changing ``load_effective_config`` or
``EffectiveConfig.get``.

Dependencies:
    tomllib: Standard-library TOML parser.

Safety:
    Reading configuration never executes code or follows includes outside
    the configuration directory.

Example:
    >>> cfg = EffectiveConfig({"update": {"channel": "prerelease"}})
    >>> cfg.get("update.channel"), cfg.get("update.hold_version")
    ('prerelease', None)
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_MISSING = object()


class EffectiveConfig:
    """Read-only view of merged configuration with dotted-key lookup.

    Instances are immutable after construction; callers receive values, not
    references to internal tables. Not thread-safe for mutation (there is
    none), safe to share for reads.
    """

    def __init__(self, data: Mapping[str, Any]) -> None:
        """Wrap already-merged configuration data.

        Args:
            data: Nested tables as produced by merging TOML files.

        Returns:
            None

        Raises:
            None
        """
        self._data: Mapping[str, Any] = data

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Return the value at ``dotted_key`` such as ``"update.channel"``.

        Args:
            dotted_key: Table path and key joined by dots.
            default: Value returned when any part of the path is absent.

        Returns:
            Any: The configured value, or ``default``.

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
        return node

    def as_dict(self) -> dict[str, Any]:
        """Return a deep copy of the merged configuration as plain dicts.

        Args:
            None

        Returns:
            dict[str, Any]: Copy safe for the caller to modify.

        Raises:
            None
        """
        return _deep_copy(self._data)


def load_effective_config(config_dir: Path) -> EffectiveConfig:
    """Merge ``config.toml`` and ``conf.d/*.toml`` from ``config_dir``.

    Later files override earlier ones key by key; tables merge recursively.
    Missing files are treated as empty so a fresh install starts on defaults.

    Args:
        config_dir: Operator configuration directory, normally ``/etc/vakr``.

    Returns:
        EffectiveConfig: The merged view.

    Raises:
        tomllib.TOMLDecodeError: If any file is not valid TOML.
        OSError: If a present file cannot be read.
    """
    files = [config_dir / "config.toml", *sorted((config_dir / "conf.d").glob("*.toml"))]
    merged: dict[str, Any] = {}
    for path in files:
        if path.is_file():
            with path.open("rb") as handle:
                _merge_into(merged, tomllib.load(handle))
    return EffectiveConfig(merged)


def _merge_into(target: dict[str, Any], overlay: Mapping[str, Any]) -> None:
    """Recursively merge ``overlay`` into ``target`` in place.

    Args:
        target: Accumulated configuration, modified in place.
        overlay: Values from the next file in precedence order.

    Returns:
        None

    Raises:
        None
    """
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge_into(target[key], value)
        else:
            target[key] = _deep_copy(value) if isinstance(value, Mapping) else value


def _deep_copy(data: Mapping[str, Any]) -> dict[str, Any]:
    """Copy nested mappings into fresh dicts.

    Args:
        data: Mapping that may contain nested mappings.

    Returns:
        dict[str, Any]: Independent copy.

    Raises:
        None
    """
    return {k: _deep_copy(v) if isinstance(v, Mapping) else v for k, v in data.items()}
