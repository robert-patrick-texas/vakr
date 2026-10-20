"""Handlers for ``vakr config`` subcommands.

Operators use these during installs, upgrades, and incidents: to see what
vakr is actually running with and why, to check an edit before a restart,
to apply config migrations after a package upgrade, and to learn what a
release added (CONF-006, CONF-009, CFGM-001 to CFGM-006). ``cmd_config``
defines the arguments; this module does the work.

Dependencies:
    vakr.config.project: vakr's catalog, layout, and rendered texts.
    vakr.lifecycle.configmerge: Upgrade, annotation, and state tracking.

Safety:
    ``show-effective``, ``validate``, ``new-options`` (without
    ``--annotate``), ``defaults`` (without ``--output``), and ``template`` are
    read-only. Output masks credentials in URLs and never prints secrets.

Example:
    $ vakr config validate
    configuration valid: 1 operator file(s), config_version 1
"""

import argparse
import sys
import textwrap
from collections.abc import Sequence

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.lifecycle.configmerge import (
    Issue,
    UpgradeError,
    annotate,
    new_options,
    read_state,
    record_version,
    redact,
    toml_literal,
    upgrade,
)
from vakr.lifecycle.configmerge.render import type_label
from vakr.paths import InstallPaths
from vakr.version import UNKNOWN_VERSION, get_version


def _report(issues: Sequence[Issue], what: str) -> None:
    """Print validation problems to stderr, one ``file:line: key: message`` each.

    Args:
        issues: Problems to print.
        what: Closing sentence, such as ``"configuration invalid"``.

    Returns:
        None

    Raises:
        None
    """
    for issue in issues:
        print(issue, file=sys.stderr)
    print(f"{what}: {len(issues)} problem(s)", file=sys.stderr)


def show_effective(args: argparse.Namespace) -> int:
    """Print every effective value as TOML, optionally with its source.

    Args:
        args: ``config_dir`` and ``sources``.

    Returns:
        int: 0 when the configuration is valid, 1 otherwise (values are
        still printed so the operator can see what would apply).

    Raises:
        OSError: If an operator file cannot be read.
    """
    result = project.inspect(args.config_dir or InstallPaths.from_environment().config_dir)
    catalog = project.catalog()
    print(f"config_version = {catalog.config_version}")
    for option in catalog.options:
        line = f"{option.key} = {toml_literal(redact(option.spec, result.values[option.key]))}"
        if args.sources:
            line += "  # " + ", ".join(str(origin) for origin in result.sources[option.key])
        print(line)
    if result.issues:
        _report(result.issues, "configuration invalid")
    return 1 if result.issues else 0


def validate(args: argparse.Namespace) -> int:
    """Validate operator files without starting anything (CONF-009).

    Args:
        args: ``config_dir``.

    Returns:
        int: 0 when valid, 1 when any problem is found.

    Raises:
        OSError: If an operator file cannot be read.
    """
    result = project.inspect(args.config_dir or InstallPaths.from_environment().config_dir)
    if result.issues:
        _report(result.issues, "configuration invalid")
        return 1
    # The portable catalog only checks that a re: keyword compiles; vakr's
    # policy package also rejects unsafe (backtracking) patterns (POLICY-005).
    from vakr.policy.errors import PolicyError
    from vakr.policy.keywords import keywords_from_config

    try:
        keywords_from_config(EffectiveConfig(result.nested(), result.sources))
    except PolicyError as exc:
        for issue in exc.issues:
            print(f"{issue.path}: {issue.reason}")
        print("configuration invalid: unsafe or invalid interface keyword")
        return 1
    current = project.catalog().config_version
    for path, version in result.versions.items():
        if version < current:
            print(f"note: {path} is at config_version {version}; run `vakr config upgrade`")
    print(f"configuration valid: {len(result.versions)} operator file(s), config_version {current}")
    return 0


def run_upgrade(args: argparse.Namespace) -> int:
    """Apply pending config migrations, then record the deployed release.

    Args:
        args: ``config_dir`` and ``dry_run``.

    Returns:
        int: 0 on success (including nothing to do), 1 when the upgrade is
        refused or fails; operator files are then unchanged.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    layout = project.layout(args.config_dir or paths.config_dir)
    catalog = project.catalog()
    try:
        result = upgrade(
            catalog, project.migrations(), layout, project.backup_root(paths), dry_run=args.dry_run
        )
    except UpgradeError as exc:
        _report(exc.issues, "config upgrade refused, operator files unchanged")
        return 1
    except OSError as exc:
        print(f"config upgrade failed, operator files unchanged: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(result.diff)
    if result.dry_run:
        print(f"dry run: {len(result.changes)} file(s) would change; nothing written")
        return 0
    if result.changes:
        count = len(result.changes)
        print(f"upgraded {count} file(s) to config_version {catalog.config_version}")
        print(f"backup: {result.backup}")
    else:
        print(f"configuration already at config_version {catalog.config_version}")
    return _record_release(paths)


def _record_release(paths: InstallPaths) -> int:
    """Record the running release so ``new-options`` knows the previous one.

    Args:
        paths: Installation layout.

    Returns:
        int: 0 when recorded (or skipped for an unbuilt checkout), 1 when the
        state file cannot be written.

    Raises:
        None
    """
    version = get_version()
    if version == UNKNOWN_VERSION:
        print("note: vakr is not installed as a package; release not recorded")
        return 0
    try:
        record_version(project.state_path(paths), version)
    except OSError as exc:
        print(f"could not record the deployed release: {exc}", file=sys.stderr)
        return 1
    return 0


def list_new_options(args: argparse.Namespace) -> int:
    """List options added since a release, optionally annotating operator files.

    Args:
        args: ``config_dir``, ``since`` (``None`` means the previously
            deployed release), and ``annotate``.

    Returns:
        int: 0 on success, 1 when annotation is refused, 2 for a bad
        ``--since``.

    Raises:
        OSError: If annotation cannot write; written files are rolled back.
    """
    paths = InstallPaths.from_environment()
    since = args.since
    if since is None:
        try:
            since = read_state(project.state_path(paths)).previous
        except ValueError, TypeError:
            since = None
    if since is None:
        print("no previous release recorded yet; pass --since VERSION", file=sys.stderr)
        return 0
    try:
        options = new_options(project.catalog(), since)
    except ValueError as exc:
        print(f"--since: {exc}", file=sys.stderr)
        return 2
    print(f"options added since {since}: {len(options)}")
    for option in options:
        print(f"  {option.key} = {toml_literal(option.default)}")
        meta = f"{type_label(option.spec)}; reload {option.reload}; since {option.since}"
        print(textwrap.indent(textwrap.fill(f"{meta}. {option.description}", 90), " " * 6))
    if not args.annotate or not options:
        return 0
    return _annotate(args, paths, options)


def _annotate(args: argparse.Namespace, paths: InstallPaths, options: Sequence) -> int:
    """Append the listed options as comments to the relevant operator files.

    Args:
        args: ``config_dir``.
        paths: Installation layout.
        options: Options to annotate.

    Returns:
        int: 0 on success, 1 when the configuration is invalid.

    Raises:
        OSError: If writing fails; written files are rolled back.
    """
    layout = project.layout(args.config_dir or paths.config_dir)
    try:
        result, added = annotate(
            project.catalog(), project.migrations(), layout, options, project.backup_root(paths)
        )
    except UpgradeError as exc:
        _report(exc.issues, "annotation refused, operator files unchanged")
        return 1
    for change in result.changes:
        print(f"annotated {change.path}")
    print(f"annotated {len(added)} option(s); {len(options) - len(added)} already set or noted")
    return 0


def write_defaults(args: argparse.Namespace) -> int:
    """Print the documented defaults, or write the packaged defaults directory.

    Args:
        args: ``output`` (directory or ``None`` for stdout) and ``markdown``.

    Returns:
        int: 0.

    Raises:
        OSError: If the output directory cannot be written.
    """
    if args.output is None:
        text = project.reference_markdown() if args.markdown else project.defaults_text()
        sys.stdout.write(text)
        return 0
    outputs = {
        project.DEFAULTS_FILE: project.defaults_text(),
        project.CATALOG_FILE: project.resource_text(project.CATALOG_FILE),
        project.MIGRATIONS_FILE: project.resource_text(project.MIGRATIONS_FILE),
        project.TEMPLATE_FILE: project.template_text(),
    }
    if args.markdown:
        outputs["configuration.md"] = project.reference_markdown()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, text in outputs.items():
        (args.output / name).write_text(text, encoding="utf-8")
        print(args.output / name)
    return 0


def print_template(args: argparse.Namespace) -> int:  # noqa: ARG001 - uniform handler signature
    """Print the starter ``config.toml`` that the package installs when none exists.

    Args:
        args: Parsed arguments (unused).

    Returns:
        int: 0.

    Raises:
        None
    """
    sys.stdout.write(project.template_text())
    return 0
