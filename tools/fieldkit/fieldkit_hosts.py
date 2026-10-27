# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Every name a monitored host goes by, so data from different tools can be joined.

MRTG may name a router ``rtr1.example.net``, SmokePing ``192.0.2.1``, and
the application ``rtr1``. The development side joins them on *aliases*:
the name as written, its first label, and the addresses it resolves to
here. Resolution uses the host's own resolver, in daemon threads with a
per-name timeout and an overall budget, so one dead DNS server cannot
stall the run (FIELD-009).

Dependencies:
    socket, threading, ipaddress: Standard library only.

Safety:
    Only forward lookups are made; nothing is written. With
    ``--pseudonymize`` every alias is pseudonymized in the bundle (the
    ``aliases`` column of a table member).

Example:
    >>> host_aliases("RTR1.example.net", ["192.0.2.1"])
    ['rtr1.example.net', 'rtr1', '192.0.2.1']
    >>> host_aliases("[2001:DB8::1]", [])
    ['2001:db8::1']
"""

import ipaddress
import socket
import threading
import time
from collections.abc import Iterable

RESOLVE_TIMEOUT = 3.0
"""Seconds one name may take to resolve."""
RESOLVE_BUDGET = 60.0
"""Seconds all names together may take."""
BATCH = 16


def canonical(host: str) -> str:
    """Lower-case a host, strip IPv6 brackets and zones, and canonicalise addresses.

    Args:
        host: Name or address.

    Returns:
        str: The canonical form.

    Raises:
        None
    """
    text = host.strip().strip("[]").split("%")[0].lower()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text


def is_address(host: str) -> bool:
    """Report whether a host is an IP literal.

    Args:
        host: Name or address.

    Returns:
        bool: True for an address.

    Raises:
        None
    """
    try:
        ipaddress.ip_address(canonical(host))
    except ValueError:
        return False
    return True


def host_aliases(host: str, resolved: Iterable[str]) -> list[str]:
    """List a host's aliases: itself, its first label, its addresses.

    Args:
        host: Name or address as configured.
        resolved: Addresses it resolved to.

    Returns:
        list[str]: Canonical aliases without repeats.

    Raises:
        None
    """
    name = canonical(host)
    label = name.split(".")[0]
    short = (
        label if "." in name and not is_address(name) and any(c.isalpha() for c in label) else ""
    )
    return list(dict.fromkeys(a for a in (name, short, *map(canonical, resolved)) if a))


def resolve_all(hosts: Iterable[str], *, timeout: float = RESOLVE_TIMEOUT,
                budget: float = RESOLVE_BUDGET) -> dict[str, list[str]]:  # fmt: skip
    """Resolve host names to addresses, bounded per name and overall.

    Args:
        hosts: Names; addresses are returned as themselves.
        timeout: Seconds per name.
        budget: Seconds for all names.

    Returns:
        dict[str, list[str]]: Each host's sorted addresses; empty when it did
        not resolve in time.

    Raises:
        None
    """
    results: dict[str, list[str]] = {}

    def work(host: str) -> None:
        """Resolve one name into ``results``.

        Args:
            host: The name.

        Returns:
            None

        Raises:
            None
        """
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_UDP)
        except OSError, UnicodeError:
            infos = []
        results[host] = sorted({canonical(str(i[4][0])) for i in infos})

    names = [h for h in dict.fromkeys(canonical(h) for h in hosts) if h and not is_address(h)]
    deadline = time.monotonic() + budget
    for start in range(0, len(names), BATCH):
        threads = [threading.Thread(target=work, args=(n,), daemon=True)
                   for n in names[start : start + BATCH]]  # fmt: skip
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(max(0.0, min(timeout, deadline - time.monotonic())))
    return {h: list(results.get(canonical(h), [])) for h in hosts}
