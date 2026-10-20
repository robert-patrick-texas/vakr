# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""The ``paths`` profile: ICMP RTT and loss to the peer and sample devices (FIELD-002).

Two-position deployments (east and west, ADR-024) replicate over the WAN,
and reachability thresholds (Appendix E.1) assume realistic RTT and loss.
This profile runs ``ping -c 20 -i 0.2`` to the ``--peer`` position and to the
first ``--ping-devices`` entries of ``--devices`` and records the summary
statistics, so development sees the real latency budget (55-70 ms between
positions at the reference site) instead of guessing.

Dependencies:
    fieldkit_context: Run context, step log, and bundle.

Safety:
    Twenty probes at 200 ms intervals is the iputils minimum for
    unprivileged users and a negligible load. ``-w`` bounds each run.

Example:
    >>> parse_ping("20 packets transmitted, 19 received, 5% packet loss, time 3805ms\n"
    ...            "rtt min/avg/max/mdev = 55.1/56.2/60.3/1.1 ms")["avg_ms"]
    56.2
    >>> ping_target("udp6:[2001:db8::1]:161")
    '2001:db8::1'
"""

import re
from typing import Any

from fieldkit_context import Context
from fieldkit_run import run_command

PING_ARGS = ("-n", "-q", "-c", "20", "-i", "0.2", "-W", "2", "-w", "30")
PING_TIMEOUT = 40.0
"""Seconds before a ping run is killed; ``-w 30`` normally ends it first."""
_SENT_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
_LOSS_RE = re.compile(r"([\d.]+)% packet loss")
_RTT_RE = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def ping_target(entry: str) -> str:
    """Reduce a net-snmp agent spec to the host part ``ping`` understands.

    Args:
        entry: ``rtr1``, ``10.0.0.1:1161``, ``udp:rtr1``, or ``udp6:[2001:db8::1]:161``.

    Returns:
        str: Host name or address without transport prefix, brackets, or port.

    Raises:
        None
    """
    host = re.sub(r"^(?:udp6?|tcp6?):", "", entry)
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def parse_ping(text: str) -> dict[str, Any]:
    """Extract counts, loss, and RTT statistics from iputils ``ping -q`` output.

    Args:
        text: Command output.

    Returns:
        dict[str, Any]: ``transmitted``, ``received``, ``loss_pct``, and
        ``min_ms``/``avg_ms``/``max_ms``/``mdev_ms``; absent fields are ``None``.

    Raises:
        None
    """
    sent, loss, rtt = _SENT_RE.search(text), _LOSS_RE.search(text), _RTT_RE.search(text)
    stats: dict[str, Any] = {
        "transmitted": int(sent[1]) if sent else None,
        "received": int(sent[2]) if sent else None,
        "loss_pct": float(loss[1]) if loss else None,
    }
    for index, key in enumerate(("min_ms", "avg_ms", "max_ms", "mdev_ms"), 1):
        stats[key] = float(rtt[index]) if rtt else None
    return stats


def collect_paths(ctx: Context) -> None:
    """Run the ``paths`` profile.

    Args:
        ctx: Run context; ``ctx.devices`` supplies the sample devices.

    Returns:
        None

    Raises:
        ValueError: If there is neither a peer nor any device to ping.
    """
    targets = [("peer", ctx.args.peer)] if ctx.args.peer else []
    targets += [("device", d) for d in ctx.devices[: ctx.args.ping_devices]]
    if not targets:
        raise ValueError("the paths profile needs --peer HOST or --devices FILE")
    results = []
    for kind, entry in targets:
        if ctx.steps.expired():
            ctx.bundle.notes.append("paths: time budget reached before every target was pinged")
            break
        label = ctx.label(entry)
        with ctx.steps.step(f"paths.{kind}.{label}") as record:
            result = run_command(["ping", *PING_ARGS, ping_target(entry)], PING_TIMEOUT)
            stats = parse_ping(result.stdout)
            if result.error or stats["received"] is None:
                record["error"] = result.error or (result.stderr.strip()[:200] or "no summary")
            results.append({"kind": kind, "target": label, **stats})
            if result.stdout:
                ctx.bundle.add_text(f"paths/ping-{kind}-{label}.txt", result.stdout)
    ctx.bundle.add_json("paths/ping.json", results)
