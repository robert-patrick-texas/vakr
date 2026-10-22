"""Admin operations: asynchronous work an administrator asks for and then follows (API-011).

The first and, in this release, only kind is the manual rediscovery
(DISC-003, DISC-010): ``OperationService`` queues it for the web UI, the
API, and ``vakr inventory rediscover``; ``CollectorOperations`` is how a
collector claims and finishes it. The bounds are in ``limits`` (Appendix
E.32).

Dependencies:
    vakr.operations.service, runs, rules, limits: The parts.
    vakr.config.loader: ``polling.reachability_strategy`` for ``operation_service``.

Safety:
    See the parts; operations carry no secret.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> from vakr.config.loader import EffectiveConfig
    >>> from vakr.ports.clock import SystemClock
    >>> type(operation_service(memory_uow_factory(), SystemClock(), EffectiveConfig({}))).__name__
    'OperationService'
"""

from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import ReachabilityStrategy
from vakr.operations.rules import RefusedError, Requested, clean_actor, refusal
from vakr.operations.runs import CollectorOperations, result_of
from vakr.operations.service import OperationService
from vakr.ports.clock import Clock
from vakr.ports.metadata import MetadataUnitOfWorkFactory


def operation_service(
    uow_factory: MetadataUnitOfWorkFactory, clock: Clock, config: EffectiveConfig
) -> OperationService:
    """Build the request side with the configured default reachability strategy.

    Args:
        uow_factory: Opens metadata units of work.
        clock: Time source.
        config: Effective configuration; an unusable strategy falls back to ``icmp_gated``.

    Returns:
        OperationService: The service.

    Raises:
        None
    """
    fallback = ReachabilityStrategy.ICMP_GATED  # the option's own default (ICMP-006)
    try:
        default = ReachabilityStrategy(config.get("polling.reachability_strategy", fallback))
    except ValueError:
        default = fallback
    return OperationService(uow_factory, clock, default_strategy=default)


__all__ = [
    "CollectorOperations",
    "OperationService",
    "RefusedError",
    "Requested",
    "clean_actor",
    "operation_service",
    "refusal",
    "result_of",
]
