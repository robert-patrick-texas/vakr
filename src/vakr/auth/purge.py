"""Bounded clean-up of expired authentication rows (SEC-010, Appendix E.12).

Expired sessions and kiosk tokens are already useless, since validation
checks expiry on every request, but their rows would pile up; quiet
login-failure runs likewise. ``purge_expired`` deletes at most
``purge_batch`` (500) rows per table per call, so one call is always
short and a backlog after a long outage clears over a few runs. The API
process runs it every few minutes.

Dependencies:
    vakr.auth.settings: Lifetimes and the batch bound.
    vakr.ports: The metadata unit of work and the clock.

Safety:
    Deletes only rows past their expiry; never users or audit events.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.ports.clock import SystemClock
    >>> purge_expired(memory_uow_factory(), SystemClock(), AuthSettings())
    PurgeCounts(sessions=0, kiosk_tokens=0, login_failures=0)
"""

from dataclasses import dataclass

from vakr.auth.settings import AuthSettings
from vakr.ports.clock import Clock
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class PurgeCounts:
    """Rows one purge pass deleted.

    Attributes:
        sessions: Expired or idle sessions.
        kiosk_tokens: Expired kiosk tokens.
        login_failures: Quiet failure runs.
    """

    sessions: int
    kiosk_tokens: int
    login_failures: int


def purge_expired(
    uow_factory: MetadataUnitOfWorkFactory, clock: Clock, settings: AuthSettings
) -> PurgeCounts:
    """Delete expired sessions and kiosk tokens and quiet failure runs, in bounded batches.

    Args:
        uow_factory: Opens metadata units of work.
        clock: Time source.
        settings: Lifetimes, reset window, and ``purge_batch``.

    Returns:
        PurgeCounts: Rows deleted.

    Raises:
        StorageError: If the deletes cannot be written.
    """
    now, batch = clock.now_utc(), settings.purge_batch
    with uow_factory() as uow:
        counts = PurgeCounts(
            uow.sessions.purge_expired(
                now=now, idle_cutoff=now - settings.session_idle, limit=batch
            ),
            uow.kiosk_tokens.purge_expired(now=now, limit=batch),
            uow.login_failures.purge(now - settings.throttle.reset_after, limit=batch),
        )
        uow.commit()
    return counts
