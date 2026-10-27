# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Pseudonymize CSV table members column by column, so joins survive (FIELD-005).

The comparison profiles write tables (MRTG targets, SmokePing targets, the
application's device and interface catalogs) whose rows are joined on the
development host by host name and address. Searching free text for names
is not enough there: a name inside ``rtr1.example.net_2`` would be missed,
and a join needs every alias mapped the same way in every bundle. So each
column is scrubbed by what its header says it holds:

* host columns (``host``, ``device``, ``poll_address``, ``slave``) and every
  word of ``aliases`` become the keyed host pseudonym (addresses map into
  the address ranges);
* address columns (``addresses``, ``target_address``, ``ip``) map each
  address and keep a ``/prefix``;
* ``mac`` keeps its notation;
* labels (``target``, ``path``, ``menu``, ``title``, ``if_alias``) keep only
  classification keywords;
* identifier and number columns are kept; anything else gets the generic
  text scrub.

Dependencies:
    csv, io: Standard library.
    fieldkit_scrub: The run's scrubber.

Safety:
    A column this module does not know is never passed through verbatim;
    it gets the generic scrub. A member that is not valid CSV gets the
    generic scrub as a whole.

Example:
    >>> from fieldkit_pseudo import Pseudonymizer
    >>> s = Scrubber(Pseudonymizer(bytes(32)))
    >>> out = scrub_table(s, "key,host,aliases\nt1,rtr1.example.net,rtr1 192.0.2.1\n")
    >>> out.splitlines()[1].split(",")[0], "rtr1" in out
    ('t1', False)
"""

import csv
import io
import re

from fieldkit_scrub import MAC_RE, Scrubber

HOST_COLUMNS = frozenset({"host", "device", "poll_address", "slave"})
HOST_LIST_COLUMNS = frozenset({"aliases"})
ADDRESS_COLUMNS = frozenset({"addresses", "target_address", "ip", "resolved"})
MAC_COLUMNS = frozenset({"mac"})
LABEL_COLUMNS = frozenset({"target", "path", "menu", "title", "if_alias"})
KEEP_COLUMNS = frozenset({
    "key", "device_key", "interface_id", "device_id", "if_index", "ifindex", "start", "end",
    "sent", "received", "median_us", "rtts_us", "state", "flags", "elapsed_s", "in_bps",
    "out_bps", "counter_bits", "if_speed", "if_high_speed", "pings", "step", "maxbytes",
    "maxbytes1", "maxbytes2", "absmax", "interval_s", "port", "snmp_version", "lines",
})  # fmt: skip
_CISCO_MAC = re.compile(r"^[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}$")


def _address(scrubber: Scrubber, item: str) -> str:
    """Map one address, keeping a ``/prefix`` suffix.

    Args:
        scrubber: The run's scrubber.
        item: Such as ``198.51.100.1/30``.

    Returns:
        str: The mapped address.

    Raises:
        None
    """
    address, slash, prefix = item.partition("/")
    return scrubber.p.ip(address) + slash + prefix


def _cell(scrubber: Scrubber, column: str, value: str) -> str:
    """Scrub one cell by its column's meaning.

    Args:
        scrubber: The run's scrubber.
        column: Lower-cased header.
        value: Cell text.

    Returns:
        str: The scrubbed cell.

    Raises:
        None
    """
    if not value or column in KEEP_COLUMNS:
        return value
    is_mac = bool(MAC_RE.fullmatch(value) or _CISCO_MAC.match(value))
    rules = (
        (HOST_COLUMNS, lambda: scrubber.p.hostname(value)),
        (HOST_LIST_COLUMNS, lambda: " ".join(scrubber.p.hostname(v) for v in value.split())),
        (ADDRESS_COLUMNS, lambda: " ".join(_address(scrubber, v) for v in value.split())),
        (MAC_COLUMNS if is_mac else frozenset(), lambda: scrubber.mac_text(value)),
        (LABEL_COLUMNS, lambda: scrubber.p.free_text(value)),
    )
    for columns, rule in rules:
        if column in columns:
            return rule()
    return scrubber.text(value)


def table_hosts(text: str) -> list[str]:
    """List the host names a table holds, to register them before later members.

    Args:
        text: CSV text with a header.

    Returns:
        list[str]: Values of the host and alias columns.

    Raises:
        None
    """
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error:
        return []
    found: list[str] = []
    for row in rows:
        for column, value in row.items():
            name = (column or "").strip().lower()
            if name in HOST_COLUMNS or name in HOST_LIST_COLUMNS:
                found += (value or "").split()
    return found


def scrub_table(scrubber: Scrubber, text: str) -> str:
    """Pseudonymize a CSV member whose first row is its header.

    Args:
        scrubber: The run's scrubber.
        text: CSV text.

    Returns:
        str: CSV text with every identifying cell replaced.

    Raises:
        None
    """
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return scrubber.text(text)
    if not rows:
        return text
    header = [h.strip().lower() for h in rows[0]]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(rows[0])
    for row in rows[1:]:
        names = header + [""] * (len(row) - len(header))
        writer.writerow(_cell(scrubber, names[i], cell) for i, cell in enumerate(row))
    return out.getvalue()
