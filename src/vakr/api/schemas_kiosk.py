"""Shapes of the kiosk endpoints: token records, the one-time token, and the request bodies (E.12).

A kiosk token record says who created it, when it expires, and when a
display last used it; it never carries the token or its digest. The token
itself appears exactly once, in the answer to the request that created it
(``KioskIssued``), so an administrator can paste it into the display.

Dependencies:
    dataclasses, pydantic: Response shapes and request bodies.
    vakr.api.paging: Paging facts.

Safety:
    ``KioskExchange.token`` and ``KioskIssued.token`` are kept out of
    ``repr``, and validation errors never echo input
    (``hide_input_in_errors``).

Example:
    >>> KioskCreate(label="noc-wall").lifetime is None
    True
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vakr.api.paging import PageInfo
from vakr.auth.kiosk_rules import MAX_LABEL
from vakr.domain.sessions import KioskToken

MAX_TOKEN_INPUT = 128
"""Longest token accepted as input; real kiosk tokens are 47 characters."""
MAX_LIFETIME_INPUT = 16
"""Longest lifetime text, such as ``365d`` or ``8760h``."""
SETUP_PATH = "/kiosk/setup"
"""Where a display's browser takes the token (the web UI's paste form)."""


@dataclass(frozen=True, slots=True)
class KioskTokenOut:
    """One kiosk token record, without the token (E.12).

    Attributes:
        id: Record UUID; ``DELETE /api/v1/kiosk-tokens/{id}`` revokes it.
        label: The display's name.
        created_by: The administrator who created it.
        created_at: Creation time (UTC).
        expires_at: Absolute expiry (UTC).
        last_used_at: Last recorded use (UTC); ``null`` when no display used it yet.
        live: False once it has expired (expired records are purged within minutes).
    """

    id: UUID
    label: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    live: bool

    @classmethod
    def of(cls, token: KioskToken, now: datetime) -> KioskTokenOut:
        """Describe a stored record.

        Args:
            token: The record.
            now: Current time (UTC).

        Returns:
            KioskTokenOut: The description.

        Raises:
            None
        """
        return cls(token.id, token.label, token.created_by, token.created_at, token.expires_at,
                   token.last_used_at, token.is_live(now))  # fmt: skip


@dataclass(frozen=True, slots=True)
class KioskTokenPage:
    """One page of kiosk tokens, by label (API-005).

    Attributes:
        rows: The records.
        page: Paging facts.
    """

    rows: tuple[KioskTokenOut, ...]
    page: PageInfo


@dataclass(frozen=True, slots=True)
class KioskIssued:
    """A new kiosk token: the record and, this once, the token.

    Attributes:
        kiosk: The record.
        token: Paste it into the display at ``setup_path``; it is shown only now.
        setup_path: The web UI page that takes it.
    """

    kiosk: KioskTokenOut
    token: str = field(repr=False)
    setup_path: str = SETUP_PATH


class KioskCreate(BaseModel):
    """Body of ``POST /api/v1/kiosk-tokens``.

    Attributes:
        label: The display's name (E.12 label rule).
        lifetime: Such as ``90d``; ``auth.kiosk_token_lifetime`` when omitted; 1 to 365 days.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    label: str = Field(min_length=1, max_length=MAX_LABEL)
    lifetime: str | None = Field(default=None, min_length=1, max_length=MAX_LIFETIME_INPUT)


class KioskExchange(BaseModel):
    """Body of ``POST /api/v1/auth/kiosk``.

    Attributes:
        token: The kiosk token (``vkk_...``) an administrator handed over.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: str = Field(min_length=1, max_length=MAX_TOKEN_INPUT, repr=False)
