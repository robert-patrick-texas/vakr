"""``vakr inventory rediscover`` and ``vakr inventory operations`` (DISC-003, DISC-010, API-011).

``rediscover DEVICE`` asks the collector to rediscover one device now, by
display name or UUID. It answers at once with the operation it queued, or
the one already queued or running for that device, and says so when no
collector has reported lately (the operation then waits; it expires after
an hour). ``--wait`` follows the operation about once a second up to
``--timeout`` (300 s, at most 3600 s) and prints how it ended.
``operations [DEVICE]`` lists recent operations, newest first.

Both run in-process on the metadata database, like ``vakr user``: run as
root, the command first becomes the ``vakr`` user, and the request is
audited with the invoking OS user (SEC-018). The API's ``POST
/api/v1/devices/{uuid}/rediscovery`` calls the same service.

Exit status of ``rediscover``: 0 queued (or, with ``--wait``, succeeded);
1 refused (unknown device, not discovered, full queue) or failed; 2 usage
error; 3 with ``--wait``, no collector reporting, the timeout passed, or
the operation expired; 4 the metadata database is unavailable.

Dependencies:
    vakr.operations: The request and read service.
    vakr.cli.inventory_support, user_support, status_stores, operation_render,
    collector_check: Store access, actor, configuration, output, collector freshness.

Safety:
    Writes only an operation and its audit record; never SNMP from this process.

Example:
    $ sudo vakr inventory rediscover core1 --wait
    queued manual rediscovery of core1 (operation 0190...)
    core1: succeeded: 24 interfaces, 1 new, 0 renumbered, 0 missing, ...
"""

import argparse
import sys
import time

from vakr.adapters.sqlite.metadata import SqliteMetadataStore, sqlite_uow_factory
from vakr.cli.collector_check import collectors_reporting
from vakr.cli.inventory_support import EXIT_INVALID, EXIT_OK, emit, guarded, require_store
from vakr.cli.operation_render import operation_lines, operation_row
from vakr.cli.status_stores import load_config
from vakr.cli.user_support import cli_actor
from vakr.domain.ids import OperationId
from vakr.domain.operations import Operation, OperationState
from vakr.domain.paging import MAX_PAGE_LIMIT, PageRequest
from vakr.operations import OperationService, limits, operation_service
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock
from vakr.ports.metadata import OperationFilter
from vakr.query.detail import find_device
from vakr.query.operation_text import outcome_words

COMMAND = "vakr inventory rediscover"
EXIT_WAITED_OUT = 3
_DONE = {OperationState.SUCCEEDED: EXIT_OK, OperationState.FAILED: EXIT_INVALID,
         OperationState.EXPIRED: EXIT_WAITED_OUT}  # fmt: skip


def _timeout(text: str) -> float:
    """Parse ``--timeout`` and enforce its bounds.

    Args:
        text: Seconds, as typed.

    Returns:
        float: Seconds.

    Raises:
        argparse.ArgumentTypeError: If it is not a number from 1 to ``WAIT_MAX_S``.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not 1 <= value <= limits.WAIT_MAX_S:
        raise argparse.ArgumentTypeError(f"timeout must be 1 to {limits.WAIT_MAX_S:g} seconds")
    return value


def register_operations(actions: argparse._SubParsersAction) -> None:
    """Add ``rediscover`` and ``operations`` under ``vakr inventory``.

    Args:
        actions: The ``inventory`` sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    ask = actions.add_parser("rediscover", help="rediscover one device now (asynchronous)")
    ask.add_argument("device", help="display name or device UUID")
    ask.add_argument("--wait", action="store_true", help="follow it until it ends")
    ask.add_argument("--timeout", type=_timeout, default=limits.WAIT_DEFAULT_S, metavar="SECONDS",
                     help="how long --wait follows it (default 300, at most 3600)")  # fmt: skip
    ask.add_argument("--json", action="store_true", help="print JSON instead of text")
    ask.set_defaults(func=run_rediscover)
    listing = actions.add_parser("operations", help="list recent admin operations")
    listing.add_argument("device", nargs="?", help="only this device's (name or UUID)")
    listing.add_argument("--limit", type=int, default=20, help="most to list (1-500, default 20)")
    listing.add_argument("--json", action="store_true", help="print JSON instead of text")
    listing.set_defaults(func=run_operations)


def _service(store: SqliteMetadataStore) -> OperationService:
    """Build the request service with the configured default reachability strategy.

    Args:
        store: The metadata store.

    Returns:
        OperationService: The service.

    Raises:
        None
    """
    config = load_config(InstallPaths.from_environment(), COMMAND)
    return operation_service(sqlite_uow_factory(store), SystemClock(), config)


@guarded
def run_rediscover(args: argparse.Namespace) -> int:
    """Queue a manual rediscovery and, with ``--wait``, follow it to the end.

    Args:
        args: ``device``, ``wait``, ``timeout``, and ``json``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    store = require_store()
    try:
        with sqlite_uow_factory(store)() as uow:
            device = find_device(uow, args.device)
        if device is None:
            print(f"vakr: no device {args.device[:64]}; see `vakr inventory list`", file=sys.stderr)
            return EXIT_INVALID
        service = _service(store)
        asked = service.request_rediscovery(device.id, actor=cli_actor())
        op = asked.operation
        how = "queued" if asked.created else f"already {op.state.value}; following"
        if not args.json:
            print(f"{how} manual rediscovery of {device.name} (operation {op.id})")
        if collectors_reporting(COMMAND) is False:
            hours = limits.PICKUP_LIMIT.total_seconds() / 3600
            print(f"vakr: no collector has reported lately, so nothing will pick this up until "
                  f"vakr-collector runs; it expires {hours:g} h after the request "
                  "(`systemctl status vakr-collector`)", file=sys.stderr)  # fmt: skip
            if args.wait:
                _show(args, op, device.name)
                return EXIT_WAITED_OUT
        if not args.wait:
            _show(args, op, device.name, quiet=not args.json)
            return EXIT_OK
        return _follow(args, service, op.id, device.name)
    finally:
        store.close()


def _follow(args: argparse.Namespace, service: OperationService, op_id: OperationId,
            name: str) -> int:  # fmt: skip
    """Read the operation about once a second until it ends or the timeout passes.

    Args:
        args: ``timeout`` and ``json``.
        service: The request service.
        op_id: The operation.
        name: The device's name, for output.

    Returns:
        int: 0 succeeded, 1 failed, 3 expired or timed out.

    Raises:
        StorageError: If the metadata store fails.
    """
    deadline = time.monotonic() + args.timeout
    seen: OperationState | None = None
    while True:
        op = service.get(op_id)
        if op is None:  # purged meanwhile: nothing left to follow
            return EXIT_WAITED_OUT
        if op.state is not seen and not args.json:
            print(f"{name}: {outcome_words(op)}", flush=True)
            seen = op.state
        if op.state in _DONE:
            if args.json:
                _show(args, op, name)
            return _DONE[op.state]
        if time.monotonic() >= deadline:
            later = f"`vakr inventory operations {name}` shows how it ends"
            print(f"vakr: still {op.state.value} after {args.timeout:g} s; it goes on: {later}",
                  file=sys.stderr)  # fmt: skip
            if args.json:
                _show(args, op, name)
            return EXIT_WAITED_OUT
        time.sleep(limits.WAIT_POLL_S)


def _show(args: argparse.Namespace, op: Operation, name: str, *, quiet: bool = False) -> None:
    """Print the operation as JSON with ``--json``; otherwise nothing more unless asked.

    Args:
        args: ``json``.
        op: The operation.
        name: The device's name.
        quiet: Print nothing in text mode (the queued line already said it).

    Returns:
        None

    Raises:
        None
    """
    if args.json:
        emit(args, [], operation_row(op, name))
    elif not quiet:
        print(f"{name}: {outcome_words(op)}")


@guarded
def run_operations(args: argparse.Namespace) -> int:
    """List recent operations, newest first.

    Args:
        args: ``device``, ``limit``, and ``json``.

    Returns:
        int: Exit status.

    Raises:
        DomainError, Unavailable: Translated by ``guarded``.
    """
    if not 1 <= args.limit <= MAX_PAGE_LIMIT:
        print(f"vakr: --limit must be 1 to {MAX_PAGE_LIMIT}", file=sys.stderr)
        return EXIT_INVALID
    store = require_store()
    try:
        where = OperationFilter()
        if args.device is not None:
            with sqlite_uow_factory(store)() as uow:
                device = find_device(uow, args.device)
            if device is None:
                print(f"vakr: no device {args.device[:64]}", file=sys.stderr)
                return EXIT_INVALID
            where = OperationFilter(device_id=device.id)
        ops = _service(store).page(PageRequest(limit=args.limit), where).items
        with sqlite_uow_factory(store)() as uow:
            names = {o.device_id: d.name for o in ops if (d := uow.devices.get(o.device_id))}
    finally:
        store.close()
    rows = [operation_row(o, names.get(o.device_id)) for o in ops]
    emit(args, operation_lines(ops, names), {"operations": rows})
    return EXIT_OK
