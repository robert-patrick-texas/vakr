"""Change ``[node]`` settings in the operator files, layer-aware and backed up (CFGM-002, E.39).

``vakr node promote`` and ``demote`` change ``node.role``,
``node.primary_url``, and ``node.peer_url`` without a hand edit. The
settings may live in ``config.toml`` or in any ``conf.d/*.toml`` drop-in,
so each key is changed where it takes effect:

- a key to set is written in the highest-precedence file that sets it;
  if none does, in the file that sets ``node.role``, else ``config.toml``;
- a key to remove is removed from every file that sets it, so a lower
  file cannot bring it back.

The edited files keep their comments and formatting (tomlkit). The whole
candidate configuration is validated, option by option and by the node
rules, before anything is written; then ``configmerge.fileio.commit``
writes a timestamped backup of every file it changes to
``/var/lib/vakr/config-backups/`` and replaces them atomically, restoring
any already replaced if a later one fails (CFGM-003).

Dependencies:
    tomlkit: Comment-preserving edits.
    vakr.config.project, vakr.lifecycle.configmerge: Layout, validation, and writes.

Safety:
    A failed check writes nothing. Files keep their mode and owner.

Example:
    >>> sorted(NODE_KEYS)
    ['peer_url', 'position', 'primary_url', 'role']
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.config.node import NodeSettings, node_problems, node_settings
from vakr.lifecycle.configmerge.fileio import commit
from vakr.lifecycle.configmerge.layering import load_config
from vakr.lifecycle.configmerge.model import Layer, read_layers
from vakr.lifecycle.configmerge.tree import drop_if_empty, ensure_table, find_node

NODE_KEYS = frozenset({"role", "position", "primary_url", "peer_url"})


class NodeEditError(ValueError):
    """The change was refused; nothing was written."""


@dataclass(frozen=True, slots=True)
class NodeEdit:
    """A checked change of the ``[node]`` settings, not yet written.

    Attributes:
        changes: ``(path, original bytes or None, new bytes)`` per file changed.
        after: The node settings the change leads to.
    """

    changes: tuple[tuple[Path, bytes | None, bytes], ...]
    after: NodeSettings

    @property
    def files(self) -> list[str]:
        """Name the files the change writes.

        Args:
            None

        Returns:
            list[str]: Their paths.

        Raises:
            None
        """
        return [str(path) for path, _, _ in self.changes]


def _sets(doc: tomlkit.TOMLDocument, key: str) -> bool:
    """Tell whether a document sets ``node.<key>``.

    Args:
        doc: A parsed operator file.
        key: A ``[node]`` key.

    Returns:
        bool: ``True`` when it does.

    Raises:
        None
    """
    table = find_node(doc, "node")
    return isinstance(table, Mapping) and key in table


def _target(docs: list[tuple[Path, tomlkit.TOMLDocument]], key: str, main: Path) -> Path:
    """Choose the file a key to set is written in.

    Args:
        docs: The parsed files, lowest precedence first.
        key: The key.
        main: ``config.toml``.

    Returns:
        Path: The highest file setting it, else the one setting ``node.role``, else ``main``.

    Raises:
        None
    """
    for wanted in (key, "role"):
        found = [path for path, doc in docs if _sets(doc, wanted)]
        if found:
            return found[-1]
    return main


def plan_node_edit(config_dir: Path, wanted: Mapping[str, str | None]) -> NodeEdit:
    """Compute and check a ``[node]`` change; write nothing.

    Args:
        config_dir: ``/etc/vakr``.
        wanted: ``[node]`` keys to set, or ``None`` to remove.

    Returns:
        NodeEdit: The checked change.

    Raises:
        NodeEditError: For an unknown key, an unreadable file, or a result that
            does not validate; the message lists the problems.
    """
    if unknown := set(wanted) - NODE_KEYS:
        raise NodeEditError(f"not a [node] setting: {', '.join(sorted(unknown))}")
    layout = project.layout(config_dir)
    try:
        layers = read_layers(layout)
        docs = [(layer.path, tomlkit.parse(layer.raw.decode("utf-8"))) for layer in layers]
    except (OSError, UnicodeDecodeError, TOMLKitError) as exc:
        raise NodeEditError(f"cannot read the configuration: {exc}") from None
    if layout.main_path not in [path for path, _ in docs]:
        docs.insert(0, (layout.main_path, tomlkit.document()))
    by_path = dict(docs)
    for key, value in wanted.items():
        if value is None:
            for _, doc in docs:
                if _sets(doc, key):
                    del doc["node"][key]
                    drop_if_empty(doc, "node")
        else:
            ensure_table(by_path[_target(docs, key, layout.main_path)], "node")[key] = value
    return _checked(layers, docs)


def _checked(layers: list[Layer], docs: list[tuple[Path, tomlkit.TOMLDocument]]) -> NodeEdit:
    """Validate the edited files as a whole configuration and list the files that changed.

    Args:
        layers: The files as read.
        docs: The edited documents, in precedence order.

    Returns:
        NodeEdit: The change.

    Raises:
        NodeEditError: If the candidate does not validate.
    """
    before = {layer.path: layer.raw for layer in layers}
    after = [(path, tomlkit.dumps(doc).encode("utf-8")) for path, doc in docs]
    result = load_config(project.catalog(), project.migrations(),
                         [Layer(path, raw) for path, raw in after])  # fmt: skip
    config = EffectiveConfig(result.nested(), result.sources, result.issues)
    problems = [str(issue) for issue in result.issues] + node_problems(config)
    if problems:
        raise NodeEditError("the change would leave an invalid configuration, so nothing "
                            f"was written: {'; '.join(problems)}")  # fmt: skip
    changes = tuple((path, before.get(path), raw) for path, raw in after
                    if before.get(path) != raw)  # fmt: skip
    return NodeEdit(changes, node_settings(config))


def write_node_edit(config_dir: Path, edit: NodeEdit, backup_root: Path) -> Path | None:
    """Back up and write a checked change atomically.

    Args:
        config_dir: ``/etc/vakr``.
        edit: From ``plan_node_edit``.
        backup_root: ``/var/lib/vakr/config-backups``.

    Returns:
        Path | None: The backup directory, or ``None`` when nothing changed.

    Raises:
        OSError: If a write fails; files already replaced are restored first.
    """
    if not edit.changes:
        return None
    return commit(config_dir, edit.changes, backup_root)
