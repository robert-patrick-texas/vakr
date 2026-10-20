"""Portable configuration layering, validation, upgrade, and new-option reporting.

Long-lived infrastructure tools accumulate operator configuration across
many releases. This library keeps that configuration working without manual
re-entry (blueprint 28.4, ADR-027):

- an option catalog (data) describes every option's type, default, release
  of introduction, and reload class;
- operator files hold only overrides, layered over catalog defaults as
  defaults < main file < lexical drop-ins < explicit overrides, with the
  source of every value tracked;
- declarative migrations, keyed by ``config_version``, rewrite operator
  files with ``tomlkit`` so comments survive, behind a backup, a diff, a dry
  run, and atomic all-or-nothing writes;
- ``new_options`` and ``annotate`` tell operators what a release added.

It is portable under blueprint section 29: it imports nothing from vakr
outside ``vakr.lifecycle``, and every project-specific value (catalog and
migration files, directories, header text) is a parameter.

Dependencies:
    tomllib: Strict parsing.
    tomlkit: Comment-preserving rewrites.

Safety:
    Reading never writes. Upgrades and annotations validate first, back up
    second, and write atomically last; any failure leaves operator files
    byte-identical (CFGM-003). Messages never contain configured values.

Example:
    >>> from pathlib import Path
    >>> catalog = load_catalog(Path("options.toml"))  # doctest: +SKIP
    >>> layers = read_layers(ConfigLayout(Path("/etc/vakr")))  # doctest: +SKIP
    >>> load_config(catalog, (), layers).issues  # doctest: +SKIP
    []
"""

from .annotate import annotate, new_options
from .catalog import Catalog, CatalogError, Option, version_key
from .catalog_file import load_catalog, parse_catalog
from .layering import load_config
from .markdown import render_markdown
from .migrations import Migration, MigrationError, load_migrations, parse_migrations
from .model import (
    DEFAULT_ORIGIN,
    VERSION_KEY,
    ConfigLayout,
    Issue,
    Layer,
    LoadResult,
    Origin,
    read_layers,
)
from .render import render_defaults, render_template, toml_literal
from .state import DeploymentState, read_state, record_version
from .upgrade import FileChange, UpgradeError, UpgradeResult, plan_upgrade, upgrade
from .values import parse_duration, redact

__all__ = [
    "DEFAULT_ORIGIN",
    "VERSION_KEY",
    "Catalog",
    "CatalogError",
    "ConfigLayout",
    "DeploymentState",
    "FileChange",
    "Issue",
    "Layer",
    "LoadResult",
    "Migration",
    "MigrationError",
    "Option",
    "Origin",
    "UpgradeError",
    "UpgradeResult",
    "annotate",
    "load_catalog",
    "load_config",
    "load_migrations",
    "new_options",
    "parse_catalog",
    "parse_duration",
    "parse_migrations",
    "plan_upgrade",
    "read_layers",
    "read_state",
    "record_version",
    "redact",
    "render_defaults",
    "render_markdown",
    "render_template",
    "toml_literal",
    "upgrade",
    "version_key",
]
