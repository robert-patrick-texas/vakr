"""Replication tokens: what a partner node presents to pull from this one (MPOS-006, E.13).

A replication token is an opaque bearer token like a kiosk token (E.12): 256
random bits behind the prefix ``vkr_``, stored only as its SHA-256 digest,
labelled, created by an administrator, and expiring absolutely. It differs
in what it grants: no role at all, only the two replication scopes, and only
on ``/api/v1/replication/*`` (ADR-024). The record shape is the kiosk
token's, so both share one storage implementation.

Dependencies:
    vakr.domain.sessions: The shared opaque-token record.

Safety:
    ``repr`` never shows the digest (inherited ``field(repr=False)``).

Example:
    >>> sorted(SCOPES)
    ['metadata_read', 'replication_read']
"""

from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.sessions import KioskToken


class Scope(StrEnum):
    """What a replication token may read (MPOS-006: least privilege)."""

    REPLICATION_READ = "replication_read"  # observations (part B, MPOS-004)
    METADATA_READ = "metadata_read"  # the metadata snapshot a peer pulls (MPOS-003)


SCOPES = frozenset(Scope)
"""Every replication token grants exactly these, and nothing outside ``/api/v1/replication``."""


@dataclass(frozen=True, slots=True)
class ReplicationToken(KioskToken):
    """A partner node's pull token: the kiosk record shape, with replication scopes instead.

    The inherited ``role`` is never consulted: a replication caller is
    admitted only by ``vakr.api.replication_auth``, which checks ``SCOPES``.
    """
