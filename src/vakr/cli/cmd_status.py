"""``vakr status``: current state of every device, or of one, from the local databases.

``vakr status`` lists devices in the 7.7 order: Attention first, then
Active, then Offline/Retired. Each line shows the status icon and label,
ICMP and SNMP state, uptime, observation age, and the collector whose view
it is. ``vakr status DEVICE`` shows one device's summary and interface
table, with the reason beside every value that is not available.
``--json`` prints the same views as a versioned JSON document.

It reads the databases in ``/var/lib/vakr`` and never creates them: on a
node without ``metadata.db`` it says "no data yet". Run as root, it first
switches to the ``vakr`` service user, like ``vakr db``.

Exit status follows the Nagios plugin convention, so monitoring scripts can
call it directly:

- 0: every listed device is Active or Offline (nothing needs attention);
- 1: at least one listed device is in Attention (red or amber);
- 3: no data yet, storage unreadable, telemetry unavailable, or no such
  device: vakr cannot say what the state is;
- 2: a usage error (argparse); Nagios CRITICAL is not used.

Dependencies:
    vakr.cli.status_stores: Opens the databases and the query service.
    vakr.cli.status_render, status_json: Text and JSON output.

Safety:
    Read-only. Never prints credentials or credential references (TUI-010).

Example:
    $ vakr status --bucket attention
    $ vakr status core1 --json
"""

import argparse
import sys

from vakr.cli.common import SubParsers
from vakr.cli.service_user import drop_to_service_user
from vakr.cli.status_json import detail_document, error_document, list_document
from vakr.cli.status_render import render_detail, render_list
from vakr.cli.status_stores import NoDataYetError, StatusStores, open_status
from vakr.domain.enums import StatusBucket
from vakr.domain.errors import StorageError, ValidationError
from vakr.domain.paging import DEFAULT_PAGE_LIMIT
from vakr.paths import InstallPaths
from vakr.ports.migrations import MigrationError
from vakr.query import DeviceListRequest, DeviceListView, DeviceSort

EXIT_OK = 0
EXIT_ATTENTION = 1
EXIT_UNKNOWN = 3
COMMAND = "vakr status"


def register(subparsers: SubParsers) -> None:
    """Add the ``status`` command.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("status", help="current state of devices (read-only)")
    parser.add_argument("device", nargs="?", metavar="DEVICE", help="one device, by name or UUID")
    parser.add_argument("--json", action="store_true", help="print a JSON document")
    parser.add_argument("--bucket", action="append", choices=[b.value for b in StatusBucket],
                        help="only devices in this bucket (repeatable)")  # fmt: skip
    parser.add_argument("--search", default="", metavar="TEXT",
                        help="only devices whose name, address, site, tags, or status "
                        "contain TEXT")  # fmt: skip
    parser.add_argument("--sort", choices=[s.value for s in DeviceSort], default="status",
                        help="list order (default: status, Attention first)")  # fmt: skip
    parser.add_argument("--include-retired", action="store_true", help="also list retired devices")
    parser.add_argument("--limit", type=int, default=DEFAULT_PAGE_LIMIT,
                        help=f"devices per page, 1-500 (default {DEFAULT_PAGE_LIMIT})")  # fmt: skip
    parser.add_argument("--offset", type=int, default=0, help="skip this many devices")
    parser.set_defaults(func=run)


def _fail(args: argparse.Namespace, message: str) -> int:
    """Report that state cannot be determined, as text or JSON.

    Args:
        args: Parsed arguments with ``json``.
        message: Operator explanation.

    Returns:
        int: ``EXIT_UNKNOWN``.

    Raises:
        None
    """
    if args.json:
        sys.stdout.write(error_document(message, EXIT_UNKNOWN))
    else:
        print(f"{COMMAND}: {message}", file=sys.stderr)
    return EXIT_UNKNOWN


def list_exit_code(view: DeviceListView, filtered: bool) -> int:
    """Return the Nagios-style exit status of a device list.

    Args:
        view: The list.
        filtered: True when a search or bucket filter was given.

    Returns:
        int: 3 when telemetry is unavailable or inventory is empty, 1 when
        any matching device needs attention, else 0.

    Raises:
        None
    """
    if not view.health.telemetry_ok or (view.total == 0 and not filtered):
        return EXIT_UNKNOWN
    return EXIT_ATTENTION if view.counts.attention else EXIT_OK


def _show_list(args: argparse.Namespace, stores: StatusStores) -> int:
    """Print the device list.

    Args:
        args: Parsed arguments.
        stores: Open stores.

    Returns:
        int: The exit status.

    Raises:
        StorageError: If the metadata store cannot be read.
        ValidationError: If the paging arguments are out of range.
    """
    request = DeviceListRequest(
        search=args.search,
        buckets=frozenset(StatusBucket(b) for b in args.bucket or ()),
        sort=DeviceSort(args.sort),
        offset=args.offset,
        limit=args.limit,
        include_retired=args.include_retired,
    )
    view = stores.queries.device_list(request)
    code = list_exit_code(view, bool(args.search or args.bucket))
    if args.json:
        sys.stdout.write(list_document(view, code))
    else:
        sys.stdout.write(render_list(view, str(stores.config.get("time.timezone", ""))))
        if view.total == 0 and code == EXIT_UNKNOWN:
            print(f"{COMMAND}: no devices in inventory yet", file=sys.stderr)
    return code


def _show_device(args: argparse.Namespace, stores: StatusStores) -> int:
    """Print one device.

    Args:
        args: Parsed arguments with ``device``.
        stores: Open stores.

    Returns:
        int: 3 when not found or telemetry is unavailable, 1 in Attention, else 0.

    Raises:
        StorageError: If the metadata store cannot be read.
    """
    view = stores.queries.device_detail(args.device)
    if view is None:
        return _fail(args, f"no device named {args.device!r}")
    if not view.health.telemetry_ok:
        code = EXIT_UNKNOWN
    else:
        attention = view.row.status.bucket is StatusBucket.ATTENTION
        code = EXIT_ATTENTION if attention else EXIT_OK
    sys.stdout.write(detail_document(view, code) if args.json else render_detail(view))
    return code


def run(args: argparse.Namespace) -> int:
    """Show current state and return the Nagios-style exit status.

    Args:
        args: Parsed arguments.

    Returns:
        int: 0 OK, 1 attention, 3 unknown (see the module docstring).

    Raises:
        None
    """
    if (problem := drop_to_service_user()) is not None:
        return _fail(args, problem)
    try:
        stores = open_status(InstallPaths.from_environment(), COMMAND)
    except NoDataYetError as exc:
        return _fail(args, str(exc))
    except (MigrationError, StorageError) as exc:
        return _fail(args, f"cannot read the metadata database: {exc}")
    try:
        return _show_device(args, stores) if args.device else _show_list(args, stores)
    except ValidationError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return 2
    except StorageError as exc:
        return _fail(args, f"cannot read the metadata database: {exc}")
    finally:
        stores.close()
