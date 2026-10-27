"""Replication endpoints a partner node pulls from (MPOS-003, MPOS-006, ADR-024, E.13).

- ``GET /api/v1/replication/metadata/state``: the replicated metadata's
  schema version, digest, and row counts, plus this node's role,
  position, and newest promotion (``{at, fork_at}`` or ``null``, E.39).
  A peer polls it every 60 s and pulls only when the digest moved.
- ``GET /api/v1/replication/metadata?kind=&cursor=&limit=``: one page of
  one replicated table (``kind`` is the table), in primary-key order.

Both need a replication token with the ``metadata_read`` scope
(``vakr.api.replication_auth``). Observation replication (part B) adds its
endpoint under the same prefix with ``replication_read``.

Dependencies:
    fastapi: Routing.
    vakr.api.replication_auth: The token dependency.

Safety:
    Pages carry password hashes (accounts replicate, MPOS-003), so nothing
    but a ``vkr_`` token can read them, and every answer is ``no-store``.

Example:
    >>> sorted(r.path for r in router.routes)
    ['/replication/metadata', '/replication/metadata/state']
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError
from vakr.api.replication_auth import MetadataReader
from vakr.api.responses import view_response
from vakr.ports.metadata.replica import ReplicaPage, ReplicaState

router = APIRouter(tags=["replication"])
Context = Annotated[ApiContext, Depends(get_context)]
DEFAULT_PAGE = 500
MAX_PAGE = 1000


def _replica(context: ApiContext, use: str, *args: Any) -> Any:
    """Run one replica operation in its own read-only unit of work (blocking).

    Args:
        context: The services.
        use: ``state`` or ``page``.
        *args: Its arguments.

    Returns:
        Any: Its result.

    Raises:
        ApiError: 503 when this store cannot replicate (not SQLite).
    """
    with context.metadata() as uow:
        replica = getattr(uow, "replica", None)
        if replica is None:
            raise ApiError(503, "replication_unavailable",
                           "this node's metadata store cannot be replicated")  # fmt: skip
        return getattr(replica, use)(*args)


@router.get("/replication/metadata/state", summary="Replicated metadata digest (replication token)")
async def state(_: MetadataReader, context: Context) -> Any:
    """Describe the replicated metadata now.

    Args:
        _: The partner node.
        context: The services.

    Returns:
        JSONResponse: Schema, digest, counts, role, and position.

    Raises:
        ApiError: 503 when the store cannot replicate.
    """
    found: ReplicaState = await call(_replica, context, "state")
    node, promotion = context.node, await call(context.promotion)
    return view_response({"schema": found.schema, "digest": found.digest, "rows": dict(found.rows),
                          "role": node.role, "position": node.position,
                          "promotion": promotion})  # fmt: skip


@router.get("/replication/metadata", summary="One page of replicated metadata (replication token)")
async def page(
    _: MetadataReader,
    context: Context,
    kind: Annotated[str, Query(min_length=1, max_length=64)],
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> Any:
    """Return one page of one replicated table.

    Args:
        _: The partner node.
        context: The services.
        kind: The table, one of the replicated tables.
        cursor: ``next_cursor`` of the previous page.
        limit: Rows, 1 to 1,000.

    Returns:
        JSONResponse: ``{table, columns, rows, next_cursor}``.

    Raises:
        ValidationError: Unknown kind or bad cursor (422).
    """
    found: ReplicaPage = await call(_replica, context, "page", kind, cursor, limit)
    return view_response({"table": found.table, "columns": list(found.columns),
                          "rows": [list(r) for r in found.rows],
                          "next_cursor": found.next_cursor})  # fmt: skip
