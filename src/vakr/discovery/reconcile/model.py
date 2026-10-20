"""What reconciliation decided for one device's discovery (IFID-003, 9.8).

``reconcile`` returns a ``Reconciliation``: every discovered row is either
a ``Match`` (``EXACT``, ``CONFIDENT_RENUMBER``, or ``NEW``) or an
``Unresolved`` case (``PROBABLE`` or ``AMBIGUOUS``) that waits for an
administrator, and stored interfaces that were not seen are ``missing``.
Candidates of unresolved rows are *held*: they are neither matched nor
missing, and receive no observations until the review closes (IFID-004).

``DiscoveryWrite`` carries what writing one discovery needs besides the
result: the device, the time, the keywords, and the stale threshold.

Dependencies:
    dataclasses: Frozen records.
    vakr.discovery.model, vakr.domain: Rows, interfaces, and outcomes.
    vakr.policy.keywords, vakr.logic.lifecycle_model: Keywords and E.6 defaults.

Safety:
    Pure data.

Example:
    >>> RULES_VERSION
    'mvp-1'
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from vakr.discovery.model import DiscoveredInterface
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.ids import DeviceId, InterfaceId
from vakr.domain.interfaces import Interface
from vakr.logic.lifecycle_model import STALE_AFTER
from vakr.policy.keywords import KeywordSet

RULES_VERSION = "mvp-1"
"""Version of the matching rules; the corpus records the version it was approved under."""


@dataclass(frozen=True, slots=True)
class Match:
    """A row matched to an identity automatically, or a new identity.

    Attributes:
        row: The discovered row.
        outcome: ``EXACT``, ``CONFIDENT_RENUMBER``, or ``NEW``.
        interface: The matched identity; ``None`` for ``NEW``.
        evidence: Why, in operator language.
    """

    row: DiscoveredInterface
    outcome: ReconcileOutcome
    interface: Interface | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Unresolved:
    """A row whose identity waits for an administrator (IFID-004).

    Attributes:
        row: The discovered row.
        outcome: ``PROBABLE`` (one candidate claimed by this row alone) or ``AMBIGUOUS``.
        candidates: Stored interfaces it may be, in UUID order.
        evidence: Agreeing and conflicting evidence.
    """

    row: DiscoveredInterface
    outcome: ReconcileOutcome
    candidates: tuple[InterfaceId, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Every decision for one discovery of one device.

    Attributes:
        matches: Automatic matches and new identities, in ifIndex order.
        unresolved: Cases for review, in ifIndex order.
        missing: Stored interfaces not seen; empty when ``ifTable`` was incomplete.
        held: Candidates of unresolved rows, in UUID order.
        rules_version: ``RULES_VERSION`` of the rules that decided.
    """

    matches: tuple[Match, ...]
    unresolved: tuple[Unresolved, ...]
    missing: tuple[Interface, ...]
    held: tuple[InterfaceId, ...]
    rules_version: str = RULES_VERSION


@dataclass(frozen=True, slots=True)
class DiscoveryWrite:
    """What a discovery write needs besides the result.

    Attributes:
        device_id: The device discovered.
        now: Discovery time (UTC), stamped on every change.
        keywords: Effective description keywords (E.15).
        stale_after: ``lifecycle.stale_after`` (LIFE-003).
    """

    device_id: DeviceId
    now: datetime
    keywords: KeywordSet
    stale_after: timedelta = STALE_AFTER
