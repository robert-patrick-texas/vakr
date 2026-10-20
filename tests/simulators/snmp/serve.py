"""Serve the synthetic fleet with snmpsim for hands-on development.

``python -m tests.simulators.snmp.serve`` writes the dataset and a matching
development credentials file under a temporary install root, starts
snmpsim on 127.0.0.1, prints ready-to-run ``vakr snmp`` commands, and serves
until Ctrl-C. Communities are generated per run and written only to the
credentials file and snmpsim's data file names; they are never printed.

Dependencies:
    argparse, signal: The command line and shutdown.
    tests.simulators.snmp: The dataset and the snmpsim runner.

Example:
    $ uv run python -m tests.simulators.snmp.serve --routers 2 --switches 1
"""

import argparse
import contextlib
import signal
import tempfile
from collections.abc import Sequence
from pathlib import Path

from tests.simulators.snmp.iosxe_dataset import device_records, reference_fleet
from tests.simulators.snmp.simkit import fake_community
from tests.simulators.snmp.snmpsim_server import snmpsim


def write_credentials(root: Path, communities: dict[str, str]) -> Path:
    """Write a development credentials file naming each device.

    Args:
        root: The temporary install root (``VAKR_ROOT``).
        communities: Community by device name.

    Returns:
        Path: The credentials file.

    Raises:
        OSError: If it cannot be written.
    """
    secrets_dir = root / "etc" / "vakr" / "secrets"
    secrets_dir.mkdir(parents=True)
    secrets_dir.chmod(0o750)
    path = secrets_dir / "credentials.toml"
    tables = [
        f'[credentials.{n}]\nversion = "2c"\ncommunity = "{c}"\n' for n, c in communities.items()
    ]
    path.write_text("\n".join(tables), encoding="utf-8")
    path.chmod(0o640)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the fleet until interrupted.

    Args:
        argv: Arguments; ``None`` means ``sys.argv[1:]``.

    Returns:
        int: 0 after Ctrl-C.

    Raises:
        RuntimeError: If snmpsim does not start.
    """
    parser = argparse.ArgumentParser(description="Serve the synthetic IOS XE fleet with snmpsim")
    parser.add_argument("--routers", type=int, default=80, help="routers to serve (default 80)")
    parser.add_argument("--switches", type=int, default=20, help="switches to serve (default 20)")
    args = parser.parse_args(argv)
    fleet = reference_fleet(args.routers, args.switches)
    communities = {spec.name: fake_community() for spec in fleet}
    with tempfile.TemporaryDirectory(prefix="vakr-snmpsim-") as tmp:
        root = Path(tmp)
        write_credentials(root, communities)
        devices = {communities[spec.name]: device_records(spec) for spec in fleet}
        sim = root / "snmpsim"
        sim.mkdir()
        with snmpsim(sim, devices) as port:
            first = fleet[0].name
            common = f"127.0.0.1 --port {port} --credential {first} --dev"
            print(f"snmpsim serves {len(fleet)} devices on 127.0.0.1:{port}. Try:")
            print(f"  VAKR_ROOT={root} uv run vakr snmp discover {common}")
            print(f"  VAKR_ROOT={root} uv run vakr snmp poll {common} --rate-after 10")
            print("Credential names are the device names. Press Ctrl-C to stop.")
            with contextlib.suppress(KeyboardInterrupt):
                signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
