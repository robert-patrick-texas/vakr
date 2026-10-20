"""``vakr credentials``: validate and list the protected credentials file (SEC-016).

``check`` applies the strict permission policy, parses the TOML, validates
every field, and checks that each SNMPv3 credential's security level, protocols,
and secrets fit together. ``list`` shows what the file defines. Both print
names, kinds, versions, and security levels, never a value. There is
deliberately no command that prints a secret: an operator who needs a value
reads the file with ``sudoedit``.

Dependencies:
    vakr.credentials: The file provider and its errors.

Safety:
    Read-only. ``--dev`` turns permission refusals into warnings for a
    developer workstation; it is a command-line flag only, never read from
    the environment. The provider is told to forget the secrets as soon as
    the summary is built.

Example:
    $ sudo vakr credentials check
    /etc/vakr/secrets/credentials.toml: OK (strict permission checks passed)
"""

import argparse
import sys
from collections.abc import Sequence

from vakr.cli.common import SubParsers
from vakr.credentials.errors import (
    CredentialFileError,
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialPermissionError,
)
from vakr.credentials.provider import CredentialSummary, FileCredentialProvider

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_MISSING = 2
POSTURE_NOTE = "SNMPv3 authPriv with SHA-2 and AES is recommended where devices support it"
_HEADINGS = ("NAME", "KIND", "VERSION", "SECURITY")


def register(subparsers: SubParsers) -> None:
    """Add ``credentials`` with its ``check`` and ``list`` actions.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser(
        "credentials", help="validate the protected credentials file (never prints secrets)"
    )
    actions = parser.add_subparsers(title="actions", metavar="ACTION", required=True)
    dev_help = "development mode: warn about unsafe permissions instead of failing"
    check = actions.add_parser("check", help="check permissions, syntax, and fields")
    check.add_argument("--dev", action="store_true", help=dev_help)
    check.set_defaults(func=run_check)
    listing = actions.add_parser("list", help="list credential names, kinds, and versions")
    listing.add_argument("--dev", action="store_true", help=dev_help)
    listing.set_defaults(func=run_list)


def run_check(args: argparse.Namespace) -> int:
    """Validate the file and print what it defines, with posture notes.

    Args:
        args: ``dev``.

    Returns:
        int: 0 when valid, 1 on problems, 2 when missing or unreadable.

    Raises:
        None
    """
    provider = FileCredentialProvider(development=args.dev)
    entries, code = _load(provider)
    if entries is None:
        return code
    tolerated = len(provider.warnings)
    provider.forget()
    mode = "strict permission checks passed"
    if args.dev:
        mode = f"development mode, {tolerated} permission problem(s) tolerated"
    print(f"{provider.path}: OK ({mode})")
    _print_table(entries)
    for entry in entries:
        if entry.recommended is False:
            print(f"note: {entry.name}: {POSTURE_NOTE} (SNMP-003)")
    return EXIT_OK


def run_list(args: argparse.Namespace) -> int:
    """Print the names, kinds, and versions the file defines.

    Args:
        args: ``dev``.

    Returns:
        int: 0 when valid, 1 on problems, 2 when missing or unreadable.

    Raises:
        None
    """
    provider = FileCredentialProvider(development=args.dev)
    entries, code = _load(provider)
    provider.forget()
    if entries is not None:
        _print_table(entries)
    return code


def _load(provider: FileCredentialProvider) -> tuple[Sequence[CredentialSummary] | None, int]:
    """Load the file, printing problems to stderr.

    Development-mode warnings are logged by the provider; without logging
    configuration Python's last-resort handler prints them to stderr.

    Args:
        provider: The provider to load from.

    Returns:
        tuple[Sequence[CredentialSummary] | None, int]: The entries and
        ``EXIT_OK``, or ``None`` and the exit status for the failure.

    Raises:
        None
    """
    try:
        entries = provider.summaries()
    except CredentialFileMissingError:
        return _fail([f"no credentials file at {provider.path}"], EXIT_MISSING)
    except CredentialFileUnreadableError as exc:
        hint = "; run as root or a member of the vakr group"
        return _fail([f"cannot read {provider.path}: {exc.reason}{hint}"], EXIT_MISSING)
    except CredentialPermissionError as exc:
        lines = [f"{provider.path}: {p}" for p in exc.problems]
        lines.append("expected: mode 0640, owner root:vakr, in a directory with mode 0750")
        return _fail(lines, EXIT_PROBLEMS)
    except CredentialFileError as exc:
        return _fail([issue.render(provider.path) for issue in exc.issues], EXIT_PROBLEMS)
    return entries, EXIT_OK


def _print_table(entries: Sequence[CredentialSummary]) -> None:
    """Print entries as an aligned table.

    Args:
        entries: Entries to print.

    Returns:
        None

    Raises:
        None
    """
    if not entries:
        print("(no credentials defined)")
        return
    rows = [_HEADINGS] + [
        (
            e.name,
            e.kind,
            e.version.value if e.version else "-",
            e.security_level.value if e.security_level else "-",
        )
        for e in entries
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_HEADINGS))]
    for row in rows:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )


def _fail(lines: list[str], code: int) -> tuple[None, int]:
    """Print problems to stderr and return the failure status.

    Args:
        lines: Problem lines.
        code: Exit status.

    Returns:
        tuple[None, int]: ``None`` and ``code``.

    Raises:
        None
    """
    for line in lines:
        print(f"vakr: {line}", file=sys.stderr)
    return None, code
