"""Observation replication endpoints the other position pulls from (MPOS-004, MPOS-005, E.13).

- ``GET /api/v1/replication/observations/state``: this node's role and
  position, and each of its own collectors with the oldest and newest seq
  its replication log holds. Every node serves it, primary and peer alike.
- ``GET /api/v1/replication/observations?collector=&after=&limit=``: one
  page of a local collector's observations after ``after``, oldest first,
  as checksummed envelopes (``vakr.replication.serving``).

Both need a replication token with the ``replication_read`` scope; operator,
kiosk, and anonymous callers are refused (``vakr.api.replication_auth``).

Dependencies:
    fastapi: Routing.
    vakr.api.replication_auth: The token dependency.

Safety:
    Read-only; every answer is ``no-store``.

Example:
    >>> sorted(r.path for r in router.routes)
    ['/replication/observations', '/replication/observations/state']
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError
from vakr.api.replication_auth import ObservationReader
from vakr.api.responses import view_response
from vakr.domain.ids import CollectorId
from vakr.replication.serving import MAX_FEED_LIMIT, ObservationSource

router = APIRouter(tags=["replication"])
Context = Annotated[ApiContext, Depends(get_context)]
DEFAULT_LIMIT = 2000


def _source(context: ApiContext) -> ObservationSource:
    """Return the observation source, or refuse when this process has none.

    Args:
        context: The services.

    Returns:
        ObservationSource: The source.

    Raises:
        ApiError: 503 when the API was built without one.
    """
    if context.observations is None:
        raise ApiError(503, "replication_unavailable", "this node serves no observations")
    return context.observations


@router.get("/replication/observations/state",
            summary="This node's collectors and log bounds (replication token)")  # fmt: skip
async def state(_: ObservationReader, context: Context) -> Any:
    """Describe this node's own collectors for the partner.

    Args:
        _: The partner node.
        context: The services.

    Returns:
        JSONResponse: ``{role, position, collectors: [...]}``.

    Raises:
        ApiError: 503 without an observation source.
    """
    collectors = await call(_source(context).state)
    node = context.node
    return view_response({"role": node.role, "position": node.position,
                          "collectors": collectors})  # fmt: skip


@router.get("/replication/observations",
            summary="One page of this node's observations (replication token)")  # fmt: skip
async def page(
    _: ObservationReader,
    context: Context,
    collector: UUID,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_FEED_LIMIT)] = DEFAULT_LIMIT,
) -> Any:
    """Return observations of one local collector after ``after``, oldest first.

    Args:
        _: The partner node.
        context: The services.
        collector: One of this node's collectors.
        after: Last seq the partner holds.
        limit: Observations, 1 to 10,000.

    Returns:
        JSONResponse: The page (``vakr.replication.serving``).

    Raises:
        NotFoundError: Another node's collector (404).
        StorageError: Telemetry unavailable (503).
    """
    found = await call(_source(context).page, CollectorId(collector), after, limit)
    return view_response(found)
