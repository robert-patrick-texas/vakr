r"""``vakr field collect``: run the packaged field kit with vakr's interpreter.

When developers need facts from production (FIELD-001..010), the operator
runs one command and sends back one encrypted file. Before vakr is installed
that command is ``uv run --python 3.15 vakr-fieldkit.pyz collect ...``; once
vakr is installed it is ``vakr field collect ...``, which runs the zipapp the
package ships at ``/opt/vakr/share/fieldkit/vakr-fieldkit.pyz`` with the
bundled Python, passing every remaining argument through unchanged, so the
two forms accept exactly the same options.

Dependencies:
    subprocess: Runs the zipapp from an argument list (no shell).
    vakr.paths: Locates the package payload (``VAKR_ROOT`` relocates it).

Safety:
    vakr adds nothing to the field kit's arguments; the community is still
    read from a protected file or a prompt by the kit itself, never from the
    command line. ``VAKR_FIELDKIT`` overrides the zipapp path for testing a
    newer kit; it is an operator choice and is printed in the error if the
    file is missing.

Example:
    $ vakr field collect --profile host,snmp,paths,vakr --devices devices.txt \
        --community-file /etc/vakr/secrets/community --peer vakr-west
    vakr-fieldkit: wrote ./vakr-field-east1-20261020T101500Z.tar.gz.p7m; next: ...
"""

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from vakr.cli.common import SubParsers
from vakr.paths import InstallPaths

FIELDKIT_ENV = "VAKR_FIELDKIT"
"""Environment variable that overrides the packaged zipapp path."""

MISSING_EXIT = 2
"""Exit status when the field kit zipapp cannot be found."""


def register(subparsers: SubParsers) -> None:
    """Add ``field`` and its ``collect`` subcommand.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "field", help="collect a field data bundle for developers (FIELD-*)"
    )
    parser.set_defaults(func=_usage(parser))
    commands = parser.add_subparsers(title="field commands", metavar="COMMAND")
    # "+" prefix chars make every "-..." argument a plain value, so all of the
    # field kit's options (and --help) pass through instead of being parsed here.
    collect = commands.add_parser(
        "collect",
        help="run vakr-fieldkit collect; see 'vakr field collect --help'",
        add_help=False,
        prefix_chars="+",
    )
    collect.add_argument("fieldkit_args", nargs=argparse.REMAINDER)
    collect.set_defaults(func=run_collect)


def _usage(parser: argparse.ArgumentParser) -> Callable[[argparse.Namespace], int]:
    """Build the handler for ``vakr field`` without a subcommand.

    Args:
        parser: The ``field`` parser whose help is printed.

    Returns:
        Callable[[argparse.Namespace], int]: Handler that prints help and
        returns 2.

    Raises:
        None
    """

    def handler(args: argparse.Namespace) -> int:  # noqa: ARG001 - uniform handler signature
        """Print ``vakr field`` usage.

        Args:
            args: Parsed arguments (unused).

        Returns:
            int: 2, a usage error.

        Raises:
            None
        """
        parser.print_help(sys.stderr)
        return 2

    return handler


def fieldkit_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the zipapp to run: ``VAKR_FIELDKIT`` or the packaged copy.

    Args:
        environ: Environment to read; ``None`` means ``os.environ``.

    Returns:
        Path: Path of ``vakr-fieldkit.pyz`` (it may not exist).

    Raises:
        None
    """
    env = os.environ if environ is None else environ
    override = env.get(FIELDKIT_ENV)
    if override:
        return Path(override)
    return InstallPaths.from_environment(env).opt_dir / "share/fieldkit/vakr-fieldkit.pyz"


def run_collect(args: argparse.Namespace) -> int:
    """Run ``vakr-fieldkit.pyz collect`` with the remaining arguments.

    Args:
        args: Parsed arguments; ``fieldkit_args`` holds everything after
            ``collect``.

    Returns:
        int: The field kit's exit status, or 2 if the zipapp is missing.

    Raises:
        None
    """
    pyz = fieldkit_path()
    if not pyz.is_file():
        print(
            f"vakr: field kit not found at {pyz}; reinstall the vakr package, or set "
            f"{FIELDKIT_ENV} to a vakr-fieldkit.pyz from the release assets",
            file=sys.stderr,
        )
        return MISSING_EXIT
    command = [sys.executable, str(pyz), "collect", *args.fieldkit_args]
    # The kit prints its own progress and final line; vakr only relays the status.
    return subprocess.run(command, check=False).returncode  # noqa: S603 - fixed argv, no shell
