# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""The ``snmp`` profile: bounded walks of the MIBs vakr discovers (FIELD-002).

Discovery and simulator fixtures need real agent behaviour: what the
system group says, how IF-MIB and ifXTable look on each platform, and which
IP-MIB table (``ipAddressTable`` or the deprecated ``ipAddrTable``) the
agent fills. For each listed device this profile runs one
``snmpbulkwalk -v2c -On -Ot -Oe -Cr10`` per subtree, then samples
``sysUpTime`` a second time at least ``--uptime-gap`` seconds after the
first, so development can check the agent's tick rate (uptime
classification, section 8.8).

Dependencies:
    fieldkit_snmpconf: Private net-snmp configuration holding the community.
    fieldkit_context: Run context, step log, and bundle.

Safety:
    Devices are walked one at a time with at most one request in flight,
    with ``--device-pause`` between devices and a per-walk timeout, to
    protect weak control planes. The community reaches net-snmp only
    through the private ``snmp.conf``; it is never an argument.

Example:
    >>> parse_ticks(".1.3.6.1.2.1.1.3.0 = 214")
    214
"""

import os
import re
import time
from typing import Any

from fieldkit_context import Context
from fieldkit_run import run_command
from fieldkit_snmpconf import private_snmp_env

WALKS = (
    ("system", "1.3.6.1.2.1.1"),
    ("ifTable", "1.3.6.1.2.1.2.2"),
    ("ifXTable", "1.3.6.1.2.1.31.1.1"),
    ("ipAddressTable", "1.3.6.1.2.1.4.34"),
    ("ipAddrTable", "1.3.6.1.2.1.4.20"),
)
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
_TICKS_RE = re.compile(r"= (?:Timeticks: )?\(?(\d+)\)?")


def parse_ticks(text: str) -> int | None:
    """Extract the ``sysUpTime`` value from ``snmpget -On -Ot`` output.

    Args:
        text: Command output.

    Returns:
        int | None: Hundredths of a second, or ``None`` if absent.

    Raises:
        None
    """
    match = _TICKS_RE.search(text)
    return int(match[1]) if match else None


def _snmp_options(ctx: Context) -> list[str]:
    """Return the shared net-snmp options; never includes the community.

    Args:
        ctx: Run context.

    Returns:
        list[str]: Version, output format, timeout, and retries.

    Raises:
        None
    """
    args = ctx.args
    return ["-v2c", "-On", "-Ot", "-Oe", "-t", str(args.snmp_timeout), "-r", str(args.snmp_retries)]


def _uptime(ctx: Context, device: str, env: dict[str, str]) -> dict[str, Any]:
    """Sample ``sysUpTime`` once.

    Args:
        ctx: Run context.
        device: Agent address.
        env: Environment with the private net-snmp configuration.

    Returns:
        dict[str, Any]: ``ticks`` (or ``None``), local ``monotonic`` time,
        and ``error`` if the request failed.

    Raises:
        None
    """
    argv = ["snmpget", *_snmp_options(ctx), device, SYS_UPTIME]
    result = run_command(argv, ctx.args.walk_timeout, env=env)
    sample: dict[str, Any] = {"ticks": parse_ticks(result.stdout), "monotonic": time.monotonic()}
    if not result.ok:
        sample["error"] = result.error or result.stderr.strip()[:200]
    return sample


def _walk(
    ctx: Context, device: str, label: str, walk: tuple[str, str], env: dict[str, str]
) -> None:
    """Run one bounded subtree walk as one step and store its output.

    Args:
        ctx: Run context.
        device: Agent address.
        label: Path label for the device.
        walk: ``(name, oid)`` of the subtree.
        env: Environment with the private net-snmp configuration.

    Returns:
        None

    Raises:
        None
    """
    name, oid = walk
    with ctx.steps.step(f"snmp.{label}.{name}") as record:
        argv = ["snmpbulkwalk", *_snmp_options(ctx), "-Cr10", device, oid]
        result = run_command(argv, ctx.args.walk_timeout, env=env)
        record["returncode"] = result.returncode
        record["records"] = sum(1 for line in result.stdout.splitlines() if line.startswith("."))
        if not result.ok:
            record["error"] = result.error or result.stderr.strip()[:200] or "no output"
        if result.stdout:
            ctx.bundle.add_text(f"snmp/{label}/{name}.txt", result.stdout, kind="snmpwalk")


def _device(ctx: Context, device: str, env: dict[str, str]) -> None:
    """Walk every subtree of one device and bracket it with uptime samples.

    Args:
        ctx: Run context.
        device: Agent address.
        env: Environment with the private net-snmp configuration.

    Returns:
        None

    Raises:
        None
    """
    label = ctx.label(device)
    first = _uptime(ctx, device, env)
    extra = tuple((f"extra-{oid}", oid) for oid in ctx.args.extra_oid)
    for walk in WALKS + extra:
        _walk(ctx, device, label, walk, env)
    # Space the two samples so the tick rate is measurable (~100 ticks/s).
    time.sleep(max(0.0, ctx.args.uptime_gap - (time.monotonic() - first["monotonic"])))
    second = _uptime(ctx, device, env)
    elapsed = second["monotonic"] - first["monotonic"]
    rate = None
    if first["ticks"] is not None and second["ticks"] is not None and elapsed > 0:
        rate = round((second["ticks"] - first["ticks"]) / elapsed, 2)
    samples = {"first": first, "second": second, "elapsed_seconds": round(elapsed, 3)}
    ctx.bundle.add_json(f"snmp/{label}/uptime.json", {**samples, "ticks_per_second": rate})


def collect_snmp(ctx: Context) -> None:
    """Run the ``snmp`` profile for every device in ``--devices``.

    Args:
        ctx: Run context; ``ctx.devices`` and ``ctx.community`` were loaded
            during preflight.

    Returns:
        None

    Raises:
        ValueError: If no devices were given.
    """
    if not ctx.devices:
        raise ValueError("the snmp profile needs --devices FILE")
    with private_snmp_env(ctx.community, os.environ) as env:
        for index, device in enumerate(ctx.devices):
            if ctx.steps.expired():
                skipped = len(ctx.devices) - index
                ctx.bundle.notes.append(f"snmp: time budget reached, {skipped} devices not walked")
                break
            if index:
                time.sleep(ctx.args.device_pause)  # protect device control planes
            _device(ctx, device, env)
