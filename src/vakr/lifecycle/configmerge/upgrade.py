r"""Upgrade operator files to the current ``config_version``, all or nothing.

Self-update runs this after installing a release (UPD-002). It applies every
pending migration to every operator file, keeping comments and formatting,
validates the result as a whole, and only then writes: backup first, then
each file atomically. Any migration or validation failure raises before a
single byte changes, so self-update can abort before restarting services
(CFGM-002, CFGM-003, CFGM-T02, CFGM-T03, CFGM-T05).

Dependencies:
    tomlkit: Comment-preserving edits.
    difflib: The unified diff shown to the operator.

Safety:
    ``dry_run`` computes the diff without writing anything. Files that are
    already current are never rewritten.

Example:
    >>> import tempfile
    >>> from vakr.lifecycle.configmerge.values import ValueSpec
    >>> from vakr.lifecycle.configmerge.catalog import Option
    >>> etc = Path(tempfile.mkdtemp())
    >>> _ = (etc / "config.toml").write_text('[update]\nchannel = "stable"\n')
    >>> option = Option("update.channel", ValueSpec("str"), "", "1.0.0", "live", "Channel.")
    >>> result = upgrade(Catalog(1, [option]), (), ConfigLayout(etc), etc / "bk", dry_run=True)
    >>> result.diff.splitlines()[3]
    '+config_version = 1'
"""

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import tomlkit

from .catalog import Catalog
from .fileio import commit
from .layering import load_config
from .migrations import BASE_VERSION, Migration, migration_path
from .model import VERSION_KEY, ConfigLayout, Issue, Layer, read_layers
from .operations import apply_migrations


class UpgradeError(Exception):
    """The upgrade was refused; operator files are unchanged.

    Attributes:
        issues: Every problem found, formatted ``file:line: key: message``.
    """

    def __init__(self, issues: Sequence[Issue]) -> None:
        """Record the problems that stopped the upgrade.

        Args:
            issues: Problems found while migrating or validating.

        Returns:
            None

        Raises:
            None
        """
        self.issues = tuple(issues)
        super().__init__(f"{len(self.issues)} problem(s); operator files unchanged")


@dataclass(frozen=True, slots=True)
class FileChange:
    """New content for one operator file.

    Attributes:
        path: The file.
        before: Original bytes, or ``None`` when the file is new.
        after: New bytes.
    """

    path: Path
    before: bytes | None
    after: bytes

    def diff(self) -> str:
        """Render a unified diff of the change.

        Args:
            None

        Returns:
            str: Diff text, ending with a newline.

        Raises:
            None
        """
        old = (self.before or b"").decode("utf-8").splitlines(keepends=True)
        new = self.after.decode("utf-8").splitlines(keepends=True)
        name = str(self.path)
        lines = difflib.unified_diff(old, new, f"{name} (before)", f"{name} (after)")
        return "".join(line if line.endswith("\n") else f"{line}\n" for line in lines)


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    """Outcome of an upgrade or dry run.

    Attributes:
        changes: Files that changed (or would change).
        backup: Backup directory, or ``None`` for dry runs and no-ops.
        dry_run: Whether nothing was written on purpose.
    """

    changes: tuple[FileChange, ...]
    backup: Path | None
    dry_run: bool

    @property
    def diff(self) -> str:
        """Concatenate the diffs of every changed file.

        Args:
            None

        Returns:
            str: Unified diff text; empty when nothing changes.

        Raises:
            None
        """
        return "".join(change.diff() for change in self.changes)


def plan_upgrade(
    catalog: Catalog, migrations: Sequence[Migration], layers: Sequence[Layer]
) -> tuple[FileChange, ...]:
    """Compute the migrated content of every out-of-date file, without writing.

    Args:
        catalog: Option catalog.
        migrations: Full migration chain.
        layers: Current operator files.

    Returns:
        tuple[FileChange, ...]: One change per file not already at the
        current ``config_version``.

    Raises:
        UpgradeError: If the current files cannot be migrated, or the
            migrated configuration is invalid.
    """
    before = load_config(catalog, migrations, layers)
    if before.issues:
        raise UpgradeError(before.issues)
    changes = [change for layer in layers if (change := _migrate(catalog, migrations, layer))]
    replaced = {change.path: change.after for change in changes}
    candidate = [Layer(layer.path, replaced.get(layer.path, layer.raw)) for layer in layers]
    after = load_config(catalog, migrations, candidate)
    if after.issues:
        raise UpgradeError(after.issues)
    return tuple(changes)


def _migrate(catalog: Catalog, migrations: Sequence[Migration], layer: Layer) -> FileChange | None:
    """Migrate one file's text and stamp the current ``config_version``.

    Args:
        catalog: Option catalog.
        migrations: Full migration chain.
        layer: A file that ``load_config`` already accepted.

    Returns:
        FileChange | None: The change, or ``None`` when the file already
        states the current version.

    Raises:
        MigrationError: Not expected, since the file was already migrated
            successfully in memory.
    """
    doc = tomlkit.parse(layer.raw.decode("utf-8"))
    stated = doc.get(VERSION_KEY)
    if stated == catalog.config_version:
        return None
    start = stated if isinstance(stated, int) else BASE_VERSION
    apply_migrations(doc, migration_path(migrations, start, catalog.config_version))
    # A file that never stated its version gets one, so later upgrades know
    # exactly where it stands.
    doc[VERSION_KEY] = catalog.config_version
    return FileChange(layer.path, layer.raw, doc.as_string().encode("utf-8"))


def upgrade(
    catalog: Catalog,
    migrations: Sequence[Migration],
    layout: ConfigLayout,
    backup_root: Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> UpgradeResult:
    """Upgrade every operator file to the catalog's ``config_version``.

    Args:
        catalog: Option catalog.
        migrations: Full migration chain.
        layout: Where operator files live.
        backup_root: Directory receiving ``<UTC timestamp>/`` backups.
        dry_run: Compute and return the diff without writing.
        now: Backup timestamp; ``None`` means now.

    Returns:
        UpgradeResult: The changes, their diff, and the backup location.

    Raises:
        UpgradeError: If migration or validation fails; nothing is written.
        OSError: If reading or writing fails; written files are rolled back.
    """
    changes = plan_upgrade(catalog, migrations, read_layers(layout))
    if dry_run or not changes:
        return UpgradeResult(changes, None, dry_run)
    triples = [(c.path, c.before, c.after) for c in changes]
    return UpgradeResult(changes, commit(layout.config_dir, triples, backup_root, now), False)
