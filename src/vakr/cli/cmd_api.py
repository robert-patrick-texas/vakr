"""``vakr api serve`` and ``vakr api openapi`` (6.2, 14, API-008).

``serve`` is the foreground API process that ``vakr-api.service`` starts:
it runs the startup checks, listens on loopback TCP for the reverse proxy
and on ``/run/vakr/api.sock`` for the local TUI and CLI, tells systemd it is
ready, and serves until SIGTERM. Operators normally use ``systemctl
start|stop vakr-api`` instead; see docs/operations/api.md. Run as root, it
first switches to the ``vakr`` service user, so the files it creates stay
the service's.

``openapi`` prints the OpenAPI document of API v1, generated from the code
(``make openapi`` writes ``docs/api/openapi.json``); ``--check`` with
``--output`` exits 1 when the file is out of date.

Exit status: ``serve`` 0 after a clean stop, 1 after a failure, 78 when a
startup check refused to start; ``openapi`` 0, or 1 when ``--check`` finds
a difference.

Dependencies:
    argparse, asyncio: Parsing and the event loop.
    vakr.app.api, vakr.api.openapi: The service and the schema (imported on
        use, so ``vakr --help`` does not load FastAPI).

Safety:
    No password or token is accepted on the command line.

Example:
    $ vakr api openapi --output docs/api/openapi.json --check
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from vakr.cli.common import SubParsers
from vakr.cli.service_user import drop_to_service_user
from vakr.paths import InstallPaths

EXIT_STALE = 1


def register(subparsers: SubParsers) -> None:
    """Add ``api`` with its ``serve`` and ``openapi`` actions.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("api", help="the vakr-api service (JSON API v1)")
    actions = parser.add_subparsers(title="actions", metavar="ACTION", required=True)
    serve = actions.add_parser("serve", help="run the API in the foreground (the service)")
    serve.add_argument("--debug", action="store_true", help="also log debug messages")
    serve.set_defaults(func=run_serve)
    spec = actions.add_parser("openapi", help="print the OpenAPI document of API v1")
    spec.add_argument("--output", type=Path, metavar="FILE", help="write it to FILE instead")
    spec.add_argument(
        "--check", action="store_true", help="with --output: exit 1 if FILE differs, write nothing"
    )
    spec.set_defaults(func=run_openapi)


def run_serve(args: argparse.Namespace) -> int:
    """Run the API until it is stopped.

    Args:
        args: Parsed arguments.

    Returns:
        int: 0 clean stop, 1 failure, 78 refused startup check.

    Raises:
        None
    """
    if (problem := drop_to_service_user()) is not None:
        print(f"vakr: {problem}", file=sys.stderr)
        return 78
    # Imported here so `vakr --help` does not pay for loading FastAPI and uvicorn.
    from vakr.app.api.entry import run_api

    level = logging.DEBUG if args.debug else logging.INFO
    return asyncio.run(run_api(InstallPaths.from_environment(), level=level))


def run_openapi(args: argparse.Namespace) -> int:
    """Print, write, or check the OpenAPI document.

    Args:
        args: ``output`` and ``check``.

    Returns:
        int: 0, or 1 when ``--check`` finds the file out of date.

    Raises:
        None
    """
    from vakr.api.openapi import openapi_text

    text = openapi_text()
    if args.output is None:
        sys.stdout.write(text)
        return 0
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            print(f"vakr: {args.output} is out of date; run make openapi", file=sys.stderr)
            return EXIT_STALE
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    return 0
