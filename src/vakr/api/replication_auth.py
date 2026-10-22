"""Who may pull from this node, and what a peer refuses (MPOS-003, MPOS-006, ADR-024).

``require_scope`` admits a partner node by ``Authorization: Bearer vkr_...``
alone: no cookie, socket account, session, or kiosk token can stand in for
it, and the token reaches nothing else (``vakr.api.principals`` answers 403
``replication_only`` for it everywhere outside ``/api/v1/replication``).

``peer_guard`` runs for every ``/api/v1`` route, beside ``csrf_guard``. On
a peer it refuses every state-changing request with 409 ``peer_read_only``,
naming the primary, except sign-in, sign-out, kiosk display setup, and kiosk
tokens, which are this node's own. A new write endpoint is refused on a peer
until it is added to ``PEER_WRITES``, which is the safe default.

Dependencies:
    fastapi: Dependencies.
    vakr.api.context: The services and node settings.

Safety:
    The token is wrapped in ``Secret`` at once and never logged or echoed.

Example:
    >>> sorted(PEER_WRITES)
    ['/api/v1/auth/', '/api/v1/kiosk-tokens']
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request

from vakr.api.context import ApiContext, call, get_context
from vakr.api.errors import ApiError
from vakr.auth.replication_tokens import Replicator
from vakr.auth.tokens import MAX_TOKEN_LENGTH, REPLICATION_PREFIX
from vakr.credentials.secret import Secret
from vakr.domain.replication import SCOPES, Scope

PEER_WRITES = frozenset({"/api/v1/auth/", "/api/v1/kiosk-tokens"})
"""Path prefixes a peer still accepts writes on: sessions and kiosk tokens are node-local."""
_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="vakr-replication"'}


def require_scope(scope: Scope) -> Callable[..., Awaitable[Replicator]]:
    """Build a dependency that admits only a live replication token with ``scope``.

    Args:
        scope: What the endpoint reads.

    Returns:
        Callable: The dependency, returning the ``Replicator``.

    Raises:
        None
    """

    async def dependency(
        request: Request, context: Annotated[ApiContext, Depends(get_context)]
    ) -> Replicator:
        """Admit or refuse the caller.

        Args:
            request: The request.
            context: The services.

        Returns:
            Replicator: The partner node.

        Raises:
            ApiError: 401 without a live ``vkr_`` bearer token.
        """
        scheme, _, value = (request.headers.get("authorization") or "").strip().partition(" ")
        value = value.strip()
        ok = scheme.lower() == "bearer" and value.startswith(REPLICATION_PREFIX)
        if ok and len(value) <= MAX_TOKEN_LENGTH and scope in SCOPES:
            found = await call(context.auth.replication.validate, Secret(value))
            if found is not None:
                return found
        raise ApiError(401, "unauthenticated", "this needs a live replication token (vkr_...) "
                       "issued by this node: sudo vakr replication token create",
                       headers=_CHALLENGE)  # fmt: skip

    return dependency


MetadataReader = Annotated[Replicator, Depends(require_scope(Scope.METADATA_READ))]
ObservationReader = Annotated[Replicator, Depends(require_scope(Scope.REPLICATION_READ))]


async def peer_guard(request: Request) -> None:
    """Refuse metadata changes on a peer (MPOS-003, MPOS-T03).

    Args:
        request: The request.

    Returns:
        None

    Raises:
        ApiError: 409 ``peer_read_only`` for a state-changing request on a peer.
    """
    if request.method in _SAFE:
        return
    refusal = get_context(request).node.refusal()
    if refusal is None or any(request.url.path.startswith(p) for p in PEER_WRITES):
        return
    raise ApiError(409, "peer_read_only", refusal)
