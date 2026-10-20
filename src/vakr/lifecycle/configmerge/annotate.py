"""Report options added by recent releases and annotate operator files with them.

New options take their defaults automatically (CFGM-001), but an operator
still needs to learn they exist. ``new_options`` lists what a release range
added; ``annotate`` appends each as a commented-out entry in the operator
file that already holds its table, so operators find it where they edit
(CFGM-004, CFGM-T04). Annotation is idempotent and never changes meaning:
only comments are added, which is verified before writing.

Dependencies:
    tomllib: Proves the annotated text parses to the same data.
    textwrap: Wraps descriptions into comment lines.

Safety:
    Options the operator already sets are skipped. Writes go through the
    same backup-and-atomic-rename path as upgrades.

Example:
    >>> from vakr.lifecycle.configmerge.values import ValueSpec
    >>> added = Option("update.hold_version", ValueSpec("str"), "", "1.1.0", "live", "Pin.")
    >>> [o.key for o in new_options(Catalog(1, [added]), "1.0.0")]
    ['update.hold_version']
"""

import re
import textwrap
import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from .catalog import Catalog, Option, version_key
from .fileio import commit
from .layering import load_config
from .lines import table_insert_index
from .migrations import Migration
from .model import VERSION_KEY, ConfigLayout, read_layers
from .render import toml_literal
from .upgrade import FileChange, UpgradeError, UpgradeResult

MARKER = "# New in {since}: {key}"
"""First line of every annotation; its presence makes annotation idempotent."""


def new_options(catalog: Catalog, since: str) -> tuple[Option, ...]:
    """List options introduced after release ``since``, in catalog order.

    Args:
        catalog: Option catalog.
        since: Release the deployment previously ran, such as ``"1.2.0"``.

    Returns:
        tuple[Option, ...]: Options whose ``since`` is newer than ``since``.

    Raises:
        ValueError: If ``since`` is not a release version.
    """
    floor = version_key(since)
    return tuple(o for o in catalog.options if version_key(o.since) > floor)


def annotate(
    catalog: Catalog,
    migrations: Sequence[Migration],
    layout: ConfigLayout,
    options: Sequence[Option],
    backup_root: Path,
    now: datetime | None = None,
) -> tuple[UpgradeResult, tuple[str, ...]]:
    """Append commented-out entries for ``options`` to the relevant operator files.

    Args:
        catalog: Option catalog.
        migrations: Full migration chain.
        layout: Where operator files live.
        options: Options to describe, usually from ``new_options``.
        backup_root: Directory receiving the timestamped backup.
        now: Backup timestamp; ``None`` means now.

    Returns:
        tuple[UpgradeResult, tuple[str, ...]]: The file changes (with backup
        location), and the keys that were annotated.

    Raises:
        UpgradeError: If the current configuration is invalid.
        OSError: If writing fails; written files are rolled back.
    """
    layers = read_layers(layout)
    state = load_config(catalog, migrations, layers)
    if state.issues:
        raise UpgradeError(state.issues)
    texts = {layer.path: layer.raw.decode("utf-8") for layer in layers}
    originals = {path: text.encode("utf-8") for path, text in texts.items()}
    if layout.main_path not in texts:
        texts = {layout.main_path: f"{VERSION_KEY} = {catalog.config_version}\n", **texts}
    pending = _plan(texts, state.operator_keys(), options, layout.main_path)
    for (path, table), group in pending.items():
        texts[path] = _with_annotations(texts[path], table, group)
    added = tuple(option.key for group in pending.values() for option in group)
    changes = tuple(
        FileChange(path, originals.get(path), text.encode("utf-8"))
        for path, text in texts.items()
        if text.encode("utf-8") != originals.get(path)
    )
    _check_unchanged_meaning(changes)
    triples = [(c.path, c.before, c.after) for c in changes]
    backup = commit(layout.config_dir, triples, backup_root, now) if changes else None
    return UpgradeResult(changes, backup, False), added


def _plan(
    texts: Mapping[Path, str], explicit: set[str], options: Sequence[Option], main: Path
) -> dict[tuple[Path, str], list[Option]]:
    """Choose the file and table each not-yet-annotated option goes to.

    Args:
        texts: Current text of every operator file, main file first.
        explicit: Keys the operator already sets (never annotated).
        options: Candidate options.
        main: Main operator file, used when no file has the option's table.

    Returns:
        dict[tuple[Path, str], list[Option]]: Options grouped by
        ``(file, table)``, in catalog order.

    Raises:
        None
    """
    pending: dict[tuple[Path, str], list[Option]] = {}
    for option in options:
        marker = MARKER.format(since=option.since, key=option.key)
        if option.key in explicit or any(_has_line(text, marker) for text in texts.values()):
            continue
        # The first file that already has the option's table is where the
        # operator edits that table; otherwise the main file.
        holders = (p for p, t in texts.items() if table_insert_index(t, option.table) is not None)
        pending.setdefault((next(holders, main), option.table), []).append(option)
    return pending


def _has_line(text: str, line: str) -> bool:
    """Report whether ``text`` contains ``line`` as a whole line.

    Args:
        text: File text.
        line: Exact line to look for.

    Returns:
        bool: ``True`` when present.

    Raises:
        None
    """
    return re.search(rf"^{re.escape(line)}\s*$", text, re.MULTILINE) is not None


def _block(option: Option) -> list[str]:
    """Render the comment lines describing one option, preceded by a blank line.

    Args:
        option: The option.

    Returns:
        list[str]: Lines without trailing newlines: marker, description,
        and the commented-out default assignment.

    Raises:
        None
    """
    description = [f"# {line}" for line in textwrap.wrap(option.description, 74)]
    marker = MARKER.format(since=option.since, key=option.key)
    return ["", marker, *description, f"# {option.name} = {toml_literal(option.default)}"]


def _with_annotations(text: str, table: str, group: Sequence[Option]) -> str:
    """Insert annotations at the end of ``[table]``, or append them with a header.

    Args:
        text: File text.
        table: Dotted table shared by every option in ``group``.
        group: Options to annotate.

    Returns:
        str: The new text, ending with a newline.

    Raises:
        None
    """
    rows = text.splitlines()
    blocks = [line for option in group for line in _block(option)]
    index = table_insert_index(text, table)
    if index is None:
        while rows and not rows[-1].strip():
            rows.pop()
        # One commented header per missing table, so uncommenting the header
        # and chosen settings yields valid TOML.
        rows += ["", f"# [{table}]", *blocks]
    else:
        rows[index:index] = blocks
    return "\n".join(rows) + "\n"


def _check_unchanged_meaning(changes: Sequence[FileChange]) -> None:
    """Prove annotations added only comments.

    Args:
        changes: Planned file changes.

    Returns:
        None

    Raises:
        RuntimeError: If any annotated file would parse differently, which
            would be a bug in this module; nothing has been written yet.
    """
    for change in changes:
        after = tomllib.loads(change.after.decode("utf-8"))
        if change.before is not None and tomllib.loads(change.before.decode("utf-8")) != after:
            raise RuntimeError(f"annotation would change the meaning of {change.path}")
