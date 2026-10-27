"""What explains a gap: collector activity, telemetry loss, and inventory (MVP-ACC-10).

A period without data is explained only by something vakr recorded
(E.35): the collector's own health reports (every 60 s while it runs, so
a silence longer than 150 s means it was stopped or restarting), its
telemetry-loss incidents (STORE-007), its spool fill level (a full spool
refuses new polls, E.24), and the inventory: when a device was added, its
admin state, and audited inventory changes. ``CollectorEvidence`` and
``DeviceEvidence`` answer the questions the classifier asks about one
instant.

Dependencies:
    bisect: Time lookups in sorted lists.
    vakr.domain: Admin states.
    vakr.query.acceptance_limits: Silence and spool thresholds.

Safety:
    Pure lookups over data the caller read.

Example:
    >>> m = 1_000_000
    >>> ev = CollectorEvidence.build([0, 60 * m, 400 * m], [], [], window=(0, 600 * m), local=True)
    >>> ev.silent(100 * m), ev.silent(30 * m), ev.silent(500 * m)
    (True, False, True)
"""

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from vakr.domain.enums import AdminState
from vakr.query.acceptance_limits import HEALTH_SILENCE_US


@dataclass(frozen=True, slots=True)
class LossRange:
    """A telemetry-loss incident as the gap report needs it (STORE-007, E.8).

    Attributes:
        kind: The incident kind, such as ``unclean_stop``.
        start_us: Start of the affected range, if known.
        end_us: End of the affected range, if known.
        classes: Affected observation types; empty when unknown (then every type).
        detail: The incident's operator sentence.
    """

    kind: str
    start_us: int | None
    end_us: int | None
    classes: tuple[str, ...]
    detail: str

    def covers(self, at_us: int, observation_class: str) -> bool:
        """Tell whether the incident accounts for one observation class at one instant.

        Args:
            at_us: The instant.
            observation_class: An ``ObservationType`` value.

        Returns:
            bool: Inside a known range, and the class is affected or unknown.

        Raises:
            None
        """
        if self.start_us is None or self.end_us is None:
            return False
        affected = not self.classes or observation_class in self.classes
        return affected and self.start_us <= at_us <= self.end_us


@dataclass(frozen=True, slots=True)
class CollectorEvidence:
    """One collector's activity, storage refusals, and losses over the report window.

    Attributes:
        silences: ``(start_us, end_us)`` stretches without a health report, sorted.
        full: Times of health reports with the spool at or above the full threshold, sorted.
        losses: Telemetry-loss incidents of this collector.
        window_end_us: End of the report window.
        local: Whether this is the node's own collector (a peer's silence may be replication lag).
    """

    silences: tuple[tuple[int, int], ...]
    full: tuple[int, ...]
    losses: tuple[LossRange, ...]
    window_end_us: int
    local: bool

    @classmethod
    def build(
        cls, reports: Sequence[int], full: Sequence[int], losses: Sequence[LossRange], *,
        window: tuple[int, int], local: bool,
    ) -> CollectorEvidence:  # fmt: skip
        """Derive the silences from the health report times.

        Args:
            reports: Health report times, sorted.
            full: Report times with a full spool, sorted.
            losses: The collector's loss incidents.
            window: ``(start_us, end_us)``.
            local: Whether the collector is this node's.

        Returns:
            CollectorEvidence: The evidence.

        Raises:
            None
        """
        edges = [window[0], *reports, window[1]]
        silences = tuple(
            (a, b) for a, b in pairwise(edges)
            if b - a > HEALTH_SILENCE_US or (not reports and b > a)
        )  # fmt: skip
        return cls(silences, tuple(full), tuple(losses), window[1], local)

    def _silence(self, at_us: int) -> tuple[int, int] | None:
        """Return the silence containing an instant, if any.

        Args:
            at_us: The instant.

        Returns:
            tuple[int, int] | None: The stretch.

        Raises:
            None
        """
        i = bisect_right(self.silences, (at_us, 1 << 62)) - 1
        if i >= 0 and self.silences[i][0] <= at_us < self.silences[i][1]:
            return self.silences[i]
        return None

    def silent(self, at_us: int) -> bool:
        """Tell whether the collector reported no health around an instant.

        Args:
            at_us: The instant.

        Returns:
            bool: Inside a silence.

        Raises:
            None
        """
        return self._silence(at_us) is not None

    def silent_to_end(self, at_us: int) -> bool:
        """Tell whether a silence containing the instant lasts until the window ends.

        Args:
            at_us: The instant.

        Returns:
            bool: The collector has not reported since, in this window.

        Raises:
            None
        """
        span = self._silence(at_us)
        return span is not None and span[1] >= self.window_end_us

    def silent_soon(self, at_us: int, within_us: int) -> bool:
        """Tell whether a silence starts within ``within_us`` after an instant (a stop).

        Args:
            at_us: The instant.
            within_us: How soon.

        Returns:
            bool: The collector stopped reporting shortly after.

        Raises:
            None
        """
        i = bisect_left(self.silences, (at_us, 0))
        return any(a - at_us <= within_us for a, _ in self.silences[i : i + 1])

    def spool_full(self, at_us: int, within_us: int) -> bool:
        """Tell whether a health report within ``within_us`` of an instant showed a full spool.

        Args:
            at_us: The instant.
            within_us: Tolerance either side.

        Returns:
            bool: The spool was full then.

        Raises:
            None
        """
        i = bisect_left(self.full, at_us - within_us)
        return i < len(self.full) and self.full[i] <= at_us + within_us

    def loss(self, at_us: int, observation_class: str) -> LossRange | None:
        """Return a loss incident that accounts for a class at an instant.

        Args:
            at_us: The instant.
            observation_class: An ``ObservationType`` value.

        Returns:
            LossRange | None: The first such incident.

        Raises:
            None
        """
        return next((x for x in self.losses if x.covers(at_us, observation_class)), None)


@dataclass(frozen=True, slots=True)
class DeviceEvidence:
    """What the inventory says about one device over the window.

    Attributes:
        created_us: When the device record was created.
        admin_state: Its admin state now.
        updated_us: When the record last changed.
        changes: Times of audited changes to the device or the whole inventory, sorted.
    """

    created_us: int
    admin_state: AdminState
    updated_us: int
    changes: tuple[int, ...]

    def offline_since(self, at_us: int) -> bool:
        """Tell whether the device is disabled or retired and has been since before an instant.

        Args:
            at_us: The instant.

        Returns:
            bool: Not scheduled then, by admin state.

        Raises:
            None
        """
        offline = self.admin_state in (AdminState.DISABLED, AdminState.RETIRED)
        return offline and self.updated_us <= at_us

    def changed_near(self, start_us: int, end_us: int) -> int | None:
        """Return the first audited change inside ``[start_us, end_us]``, if any.

        Args:
            start_us: Start.
            end_us: End.

        Returns:
            int | None: The change time.

        Raises:
            None
        """
        i = bisect_left(self.changes, start_us)
        return self.changes[i] if i < len(self.changes) and self.changes[i] <= end_us else None
