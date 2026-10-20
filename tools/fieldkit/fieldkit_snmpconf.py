# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Keep the SNMP community out of argv, the process table, and the bundle (FIELD-004).

``snmpbulkwalk -c COMMUNITY`` would put the community in ``/proc/<pid>/cmdline``
where any local user can read it with ``ps``. Instead the field kit reads
the community from a file only its owner can read (or from a hidden
prompt), writes it to an ``snmp.conf`` (``defVersion 2c`` and
``defCommunity``) at mode 0600 inside a private 0700 temporary directory,
and points net-snmp there with ``SNMPCONFPATH``. ``SNMP_PERSISTENT_DIR``
points at the same directory, so net-snmp writes no state under ``~/.snmp``
or ``/var/lib/snmp`` (FIELD-009). The directory is deleted when collection
ends. This was verified against net-snmp 5.9.4 and a local ``snmpd``:
``defCommunity`` is honoured, and a wrong one gets no response.

Dependencies:
    getpass, tempfile: Hidden prompt and private temporary directory.

Safety:
    Error messages never include the community. Communities that net-snmp's
    config parser would alter (control characters, a leading double quote,
    leading or trailing blanks, non-ASCII) are refused rather than sent
    altered. Device entries must start with a letter, digit, or ``[``, so
    none can be mistaken for a command-line option.

Example:
    >>> validate_community("n0t-a-real-one")
    >>> parse_devices("rtr1  # core\n10.0.0.2\n", "devices.txt", 10)
    ['rtr1', '10.0.0.2']
"""

import getpass
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path

_DEVICE_RE = re.compile(r"^(?:(?:udp6?|tcp6?):)?[A-Za-z0-9\[][A-Za-z0-9._:\[\]%-]*$")
_OID_RE = re.compile(r"^\.?[0-9]+(?:\.[0-9]+)+$")


class CredentialError(Exception):
    """The community is missing, unprotected, or unusable; never carries it."""


def validate_community(value: str) -> None:
    """Refuse communities that net-snmp's ``snmp.conf`` cannot carry verbatim.

    Args:
        value: Candidate community string.

    Returns:
        None

    Raises:
        CredentialError: If the value is empty, too long, or contains
            characters net-snmp would alter.
    """
    if not value:
        raise CredentialError("the SNMP community is empty")
    if len(value) > 255:
        raise CredentialError("the SNMP community is longer than 255 characters")
    if any(not 0x20 <= ord(c) <= 0x7E for c in value):
        raise CredentialError("the SNMP community must be printable ASCII")
    if value != value.strip() or value.startswith('"'):
        raise CredentialError(
            "the SNMP community starts or ends with a blank or starts with a double quote, "
            "which net-snmp's config parser would change"
        )


def read_community(path: Path | None) -> str:
    """Read the community from a protected file, or prompt for it.

    Args:
        path: ``--community-file``; the first non-empty line not starting
            with ``#`` is the community. ``None`` prompts on the terminal.

    Returns:
        str: The validated community.

    Raises:
        CredentialError: If the file is accessible to group or others, is
            not a regular file, holds no community, or there is no terminal
            to prompt on.
        OSError: If the file cannot be read.
    """
    if path is None:
        if not sys.stdin.isatty():
            raise CredentialError("give --community-file FILE (no terminal for a prompt)")
        value = getpass.getpass("SNMPv2c community (input hidden): ")
    else:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise CredentialError(f"{path} is not a regular file")
        if mode & 0o044:
            raise CredentialError(f"{path} is readable by group or others; run: chmod 600 {path}")
        lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
        value = next((line for line in lines if line and not line.startswith("#")), "")
    validate_community(value)
    return value


@contextmanager
def private_snmp_env(community: str, base: Mapping[str, str]) -> Generator[dict[str, str]]:
    """Provide a child environment whose net-snmp config holds the community.

    Args:
        community: Validated SNMPv2c community.
        base: Environment to extend, normally ``os.environ``.

    Returns:
        Generator[dict[str, str]]: Environment for ``snmpbulkwalk``/``snmpget``.

    Raises:
        OSError: If the private directory or file cannot be created.
    """
    directory = Path(tempfile.mkdtemp(prefix="fieldkit-snmp-"))  # created 0700
    try:
        conf = directory / "snmp.conf"
        fd = os.open(conf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"defVersion 2c\ndefCommunity {community}\n")
        env = dict(base)
        # Only this directory is searched, so ~/.snmp and /etc/snmp cannot
        # override the community or version.
        env["SNMPCONFPATH"] = str(directory)
        env["SNMP_PERSISTENT_DIR"] = str(directory)
        yield env
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def check_host(entry: str, where: str) -> str:
    """Validate one device or peer entry.

    Args:
        entry: Host name, address, or net-snmp agent spec such as
            ``udp6:[2001:db8::1]:161``.
        where: Location for the error message, such as ``devices.txt:4``.

    Returns:
        str: The entry, unchanged.

    Raises:
        ValueError: If the entry could be read as an option or holds
            characters no host name or address uses.
    """
    if len(entry) > 253 or not _DEVICE_RE.match(entry):
        raise ValueError(f"{where}: not a host name or address: {entry!r}")
    return entry


def parse_devices(text: str, source: str, limit: int) -> list[str]:
    """Parse a device list: one host per line, ``#`` starts a comment.

    Args:
        text: File content.
        source: File name for error messages.
        limit: Maximum number of distinct devices (``--max-devices``).

    Returns:
        list[str]: Distinct entries in file order.

    Raises:
        ValueError: If an entry is invalid or there are too many.
    """
    devices: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        entry = line.split("#", 1)[0].strip()
        if entry and check_host(entry, f"{source}:{number}") not in devices:
            devices.append(entry)
    if len(devices) > limit:
        raise ValueError(f"{source} lists {len(devices)} devices; --max-devices is {limit}")
    return devices


def check_oid(oid: str) -> str:
    """Validate a numeric ``--extra-oid`` subtree.

    Args:
        oid: Dotted numeric OID, with or without a leading dot.

    Returns:
        str: The OID without a leading dot.

    Raises:
        ValueError: If it is not a numeric OID.
    """
    if not _OID_RE.match(oid):
        raise ValueError(f"--extra-oid must be numeric, such as 1.3.6.1.2.1.10.7: {oid!r}")
    return oid.lstrip(".")
