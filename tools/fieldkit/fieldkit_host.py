# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""The ``host`` profile: facts about the monitoring host itself (FIELD-002).

Before vakr is installed on a production host, development needs to know
what it will run on: OS release and kernel, CPU and memory, disk space for
the SQLite telemetry store, whether time is synchronized (freshness and
uptime math depend on it), whether unprivileged ICMP sockets are allowed
(``net.ipv4.ping_group_range``, ADR-023), the Python and OpenSSL versions,
the proxy environment, and whether GitHub is reachable for self-update
(FIELD-010).

Dependencies:
    fieldkit_egress: Step-by-step HTTPS reachability test.
    fieldkit_context: Run context, step log, and bundle.

Safety:
    Read-only: files are read, commands only report. Proxy URLs are
    recorded with credentials redacted.

Example:
    >>> cpu_model("processor : 0\nmodel name : Example CPU @ 2.0GHz\n")
    'Example CPU @ 2.0GHz'
"""

import os
import platform
import shutil
import ssl
import sys
from pathlib import Path
from typing import Any

from fieldkit_context import Context
from fieldkit_egress import OS_CA_BUNDLES, probe
from fieldkit_tunnel import redact_url

HOST_FILES = (
    ("os-release.txt", "/etc/os-release"),
    ("meminfo.txt", "/proc/meminfo"),
    ("ping_group_range.txt", "/proc/sys/net/ipv4/ping_group_range"),
)
PROXY_VARIABLES = (
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
)  # fmt: skip
COMMAND_TIMEOUT = 20.0
"""Seconds allowed for each host command; all of them answer instantly."""


def cpu_model(cpuinfo: str) -> str:
    """Return the first ``model name`` from ``/proc/cpuinfo`` text.

    Args:
        cpuinfo: File content.

    Returns:
        str: CPU model, or ``""`` if the field is absent (some ARM kernels).

    Raises:
        None
    """
    for line in cpuinfo.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "model name":
            return value.strip()
    return ""


def _read(path: str) -> str:
    """Read a small text file, returning ``""`` if it is absent.

    Args:
        path: File to read.

    Returns:
        str: Content or ``""``.

    Raises:
        None
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def system_facts() -> dict[str, Any]:
    """Collect interpreter, kernel, CPU, and TLS-store facts.

    Args:
        None

    Returns:
        dict[str, Any]: JSON-ready facts.

    Raises:
        None
    """
    uname = os.uname()
    return {
        "uname": {
            "sysname": uname.sysname,
            "nodename": uname.nodename,
            "release": uname.release,
            "version": uname.version,
            "machine": uname.machine,
        },
        "cpu_count": os.cpu_count(),
        "process_cpu_count": os.process_cpu_count(),
        "cpu_model": cpu_model(_read("/proc/cpuinfo")),
        "python": {"version": sys.version, "executable": sys.executable},
        "python_platform": platform.platform(),
        "ssl_library": ssl.OPENSSL_VERSION,
        "ssl_default_paths": ssl.get_default_verify_paths()._asdict(),
        "os_ca_bundles": {path: Path(path).is_file() for path in OS_CA_BUNDLES},
    }


def proxy_environment(environ: dict[str, str]) -> dict[str, str | None]:
    """Report each proxy variable as present (redacted) or absent.

    Args:
        environ: Process environment.

    Returns:
        dict[str, str | None]: Variable to redacted value, or ``None``.

    Raises:
        None
    """
    return {
        name: redact_url(environ[name]) if name in environ else None for name in PROXY_VARIABLES
    }


def _egress(ctx: Context) -> None:
    """Run the GitHub reachability test and store one record per URL.

    Args:
        ctx: Run context.

    Returns:
        None

    Raises:
        None
    """
    args, info = ctx.args, ctx.info
    targets = [("GET", u) for u in args.egress_url or info.egress_get]
    targets += [("HEAD", u) for u in args.egress_head or info.egress_head]
    options = {
        "proxy": args.proxy,
        "ca_file": args.ca_file,
        "timeout": args.egress_timeout,
        "agent": f"{info.tool_name}/{info.version}",
        "environ": dict(os.environ),
    }
    results = []
    for method, url in targets:
        with ctx.steps.step(f"host.egress.{method.lower()}") as record:
            result = probe(method, url, options)
            record["url"] = url
            if result.get("error"):
                record["error"] = result["error"]
            results.append(result)
    ctx.bundle.add_json("host/egress.json", results)


def collect_host(ctx: Context) -> None:
    """Run the ``host`` profile.

    Args:
        ctx: Run context.

    Returns:
        None

    Raises:
        None
    """
    with ctx.steps.step("host.files"):
        for member, path in HOST_FILES:
            text = _read(path)
            if text:
                ctx.bundle.add_text(f"host/{member}", text)
        ctx.bundle.add_json("host/system.json", system_facts())
        ctx.bundle.add_json("host/proxy-environment.json", proxy_environment(dict(os.environ)))
    commands = [
        ("df", ["df", "-P", "-T", "-k"]),
        ("timedatectl", ["timedatectl", "show"]),
        ("openssl-version", [ctx.args.openssl, "version", "-a"]),
    ]
    if shutil.which("chronyc"):
        commands.append(("chronyc-tracking", ["chronyc", "-n", "tracking"]))
    for name, argv in commands:
        ctx.capture(f"host.{name}", f"host/{name}.txt", argv, COMMAND_TIMEOUT)
    if not ctx.args.no_egress:
        _egress(ctx)
