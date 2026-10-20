"""Language-neutral schedule hashing and collector naming (Appendix E.11, SCHED-001, SCHED-003).

Where a device's poll falls inside its interval comes from a hash, so every
restart, every host, and a future Go collector compute the same schedule.
E.11 fixes the algorithm: the first 8 bytes of SHA-256 over the UTF-8 input,
read as a big-endian unsigned integer. Python's built-in ``hash()`` is never
used, because it is salted per process and would move every device's poll
time on each restart.

The collector name defaults to the Linux short hostname (SCHED-001). The DNS
suffix is dropped and the name is lower-cased, so ``Vakr-East.example.net``
and ``vakr-east`` give the same phase (SCHED-T01).

Dependencies:
    hashlib: SHA-256.

Safety:
    Pure; nothing secret is hashed. Hash inputs are names and UUIDs only.

Example:
    >>> hex(hash64("abc"))
    '0xba7816bf8f01cfea'
    >>> short_hostname("Vakr-East.example.net.")
    'vakr-east'
"""

import hashlib
import re

HASH_BYTES = 8  # E.11: 64 bits keep modulo bias below 1e-11 for any interval
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
"""Collector names: one DNS-label-like token, so they are safe in paths and hash inputs."""


def hash64(text: str) -> int:
    """Return the E.11 schedule hash of ``text``.

    Args:
        text: Hash input, such as a collector name or ``name + "/" + uuid``.

    Returns:
        int: First 8 bytes of SHA-256 over the UTF-8 text, big-endian, in
        ``[0, 2**64)``.

    Raises:
        None
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:HASH_BYTES], "big")


def short_hostname(hostname: str) -> str:
    """Reduce a host name to its lower-case first label (SCHED-001).

    Args:
        hostname: Host name as the system reports it, possibly fully qualified.

    Returns:
        str: The first DNS label, lower-cased.

    Raises:
        ValueError: If no usable label remains.
    """
    label = hostname.strip().split(".", 1)[0].lower()
    if not _NAME.fullmatch(label):
        raise ValueError("the host name has no usable first label for a collector name")
    return label


def resolve_collector_name(configured: str, hostname: str) -> str:
    """Pick the collector name: the configured one, else the short hostname.

    A configured name is used exactly as written (after trimming), because
    an administrator who sets it expects that exact phase input.

    Args:
        configured: ``scheduler.collector_name``; empty means "use the host name".
        hostname: The system host name, used when ``configured`` is empty.

    Returns:
        str: The collector name used for phase and order hashing.

    Raises:
        ValueError: If the chosen name is not a valid collector name.
    """
    name = configured.strip()
    if not name:
        return short_hostname(hostname)
    if not _NAME.fullmatch(name):
        raise ValueError(
            "scheduler.collector_name must be 1-63 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return name
