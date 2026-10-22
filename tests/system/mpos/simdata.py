"""Write the simulated fleet for the two-position harness, on the host (MPOS-T01).

``python -m tests.system.mpos.simdata OUT`` writes everything the
containers need into ``OUT``:

- ``sim/data/<community>.snmprec``: one IOS XE device per community, from
  the simulator dataset (ADR-034). snmpsim picks the data file by
  community, so one responder on several addresses serves every device;
- ``inventory.toml``: the same devices for both nodes, at addresses
  ``PREFIX.11`` onwards, each naming its own credential;
- ``credentials.toml``: the generated communities (mode 0600). They exist
  only in this file and in the data file names, never on a command line or
  in output;
- ``devices.tsv``: name and address per line, for the shell;
- ``wheels.txt``: URL and SHA-256 of the locked snmpsim and pysmi wheels
  (snmpsim imports the ``pysmi`` package at start), so the harness
  downloads exactly what ``uv.lock`` pins and checks each hash.

Dependencies:
    argparse, tomllib: The command line and ``uv.lock``.
    tests.simulators.snmp: The dataset, communities, and ``.snmprec`` writer.

Safety:
    Communities are generated per run (``fake_community``) and written with
    mode 0600; nothing printed carries one.

Example:
    $ uv run python -m tests.system.mpos.simdata /tmp/mpos --devices 8
"""

import argparse
import tomllib
from collections.abc import Sequence
from pathlib import Path

from tests.simulators.snmp.iosxe_dataset import device_records, reference_fleet
from tests.simulators.snmp.simkit import fake_community
from tests.simulators.snmp.snmprec import write_snmprec

REPO = Path(__file__).resolve().parents[3]
FIRST_HOST = 11
"""Last octet of the first device address; .2 to .10 are the simulator and the nodes."""
MAX_DEVICES = 200
SIM_WHEELS = ("snmpsim", "pysmi")
"""Pure-Python wheels the simulator adds to the package's interpreter, which has pysnmp."""


def locked_wheel(lock: Path, name: str) -> tuple[str, str]:
    """Return the URL and SHA-256 of the one wheel ``uv.lock`` pins for a package.

    Args:
        lock: The repository's ``uv.lock``.
        name: The package.

    Returns:
        tuple[str, str]: Wheel URL and hex digest.

    Raises:
        LookupError: If the lock has no single wheel for it.
    """
    packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
    wheels = [w for p in packages if p["name"] == name for w in p.get("wheels", [])]
    if len(wheels) != 1:
        raise LookupError(f"expected one {name} wheel in {lock}, found {len(wheels)}")
    return wheels[0]["url"], wheels[0]["hash"].removeprefix("sha256:")


def inventory_text(names: Sequence[str], prefix: str, interval: str) -> str:
    """Render the inventory both nodes apply.

    Args:
        names: Device names, in address order.
        prefix: The first three octets of the device network, with a dot.
        interval: Poll interval for every device, such as ``"30s"``.

    Returns:
        str: TOML for ``/etc/vakr/inventory.toml``.

    Raises:
        None
    """
    blocks = [
        f'[[devices]]\nname = "{name}"\naddress = "{prefix}{FIRST_HOST + i}"\n'
        f'credential = "{name}"\ninterval = "{interval}"\n'
        for i, name in enumerate(names)
    ]
    return "# The two-position harness's fleet (MPOS-T01).\n\n" + "\n".join(blocks)


def write_fleet(out: Path, devices: int, prefix: str, interval: str) -> None:
    """Write the data files, inventory, credentials, and device list into ``out``.

    Args:
        out: An empty or new directory.
        devices: How many devices, 1 to ``MAX_DEVICES``.
        prefix: The first three octets of the device network, with a dot.
        interval: Poll interval for every device.

    Returns:
        None

    Raises:
        ValueError: If ``devices`` is out of range.
        OSError: If a file cannot be written.
    """
    if not 1 <= devices <= MAX_DEVICES:
        raise ValueError(f"devices must be 1 to {MAX_DEVICES}")
    fleet = reference_fleet(routers=devices - devices // 4, switches=devices // 4)
    data = out / "sim" / "data"
    data.mkdir(parents=True, exist_ok=True)
    creds: list[str] = []
    for spec in fleet:
        community = fake_community("mpos")
        write_snmprec(data / f"{community}.snmprec", device_records(spec))
        creds.append(f'[credentials.{spec.name}]\nversion = "2c"\ncommunity = "{community}"\n')
    names = [spec.name for spec in fleet]
    (out / "inventory.toml").write_text(inventory_text(names, prefix, interval), encoding="utf-8")
    secret = out / "credentials.toml"
    secret.touch(mode=0o600)
    secret.write_text("\n".join(creds), encoding="utf-8")
    rows = [f"{name}\t{prefix}{FIRST_HOST + i}\n" for i, name in enumerate(names)]
    (out / "devices.tsv").write_text("".join(rows), encoding="utf-8")
    wheels = [locked_wheel(REPO / "uv.lock", name) for name in SIM_WHEELS]
    (out / "wheels.txt").write_text("".join(f"{u} {d}\n" for u, d in wheels), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Write the fleet.

    Args:
        argv: Arguments; ``None`` means ``sys.argv[1:]``.

    Returns:
        int: 0 on success.

    Raises:
        ValueError, LookupError, OSError: As ``write_fleet``.
    """
    parser = argparse.ArgumentParser(description="Write the two-position harness's fleet")
    parser.add_argument("out", type=Path, help="output directory")
    parser.add_argument("--devices", type=int, default=8, help="simulated devices (default 8)")
    parser.add_argument("--prefix", default="172.29.71.", help="device network, three octets")
    parser.add_argument("--interval", default="60s", help="poll interval (default 60s)")
    args = parser.parse_args(argv)
    write_fleet(args.out, args.devices, args.prefix, args.interval)
    print(f"simdata: {args.devices} devices in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
