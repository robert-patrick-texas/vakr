"""This node's role in a two-position pair: settings, validation, and the peer refusal (MPOS-003).

``[node] role`` is ``primary`` (the default: a single node is a primary
without a peer, and behaves exactly as before) or ``peer``. A peer pulls
its metadata from ``node.primary_url`` and refuses local metadata edits,
naming the primary (MPOS-003, ADR-024). ``node.position`` labels the
vantage point in views; a peer must have one, so the two positions can be
told apart. Each node pulls the other's observations from its partner: a
peer from ``primary_url``, a primary from ``node.peer_url`` (MPOS-004).

Dependencies:
    re, urllib.parse: Label and URL checks.
    vakr.config.loader: The effective configuration.

Safety:
    Pure functions of the configuration; nothing here reads a file.

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> peer = EffectiveConfig({"node": {"role": "peer", "position": "west",
    ...                                  "primary_url": "https://vakr-east.example.net"}})
    >>> node_problems(peer), node_settings(peer).is_peer
    ([], True)
    >>> node_settings(peer).refusal().split(": ")[0]
    'this node is a peer; its primary is https://vakr-east.example.net'
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from vakr.config.loader import EffectiveConfig

PRIMARY = "primary"
PEER = "peer"
MAX_POSITION = 32
_POSITION = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,30}[A-Za-z0-9])?")
TOKEN_FILE = "replication.token"  # noqa: S105  # a file name
"""Name of the partner's pull token under ``/etc/vakr/secrets`` (E.13)."""


@dataclass(frozen=True, slots=True)
class NodeSettings:
    """The ``[node]`` table, validated.

    Attributes:
        role: ``primary`` or ``peer``.
        position: Vantage-point label, such as ``east``; may be empty on a primary.
        primary_url: The primary's HTTPS base URL on a peer; empty on a primary.
        peer_url: The peer's HTTPS base URL on a primary; empty without a peer.
    """

    role: str = PRIMARY
    position: str = ""
    primary_url: str = ""
    peer_url: str = ""

    @property
    def partner_url(self) -> str:
        """Return where this node pulls the other position's observations from.

        Args:
            None

        Returns:
            str: ``primary_url`` on a peer, ``peer_url`` on a primary; may be empty.

        Raises:
            None
        """
        return self.primary_url if self.is_peer else self.peer_url

    @property
    def is_peer(self) -> bool:
        """Whether this node pulls metadata from a primary.

        Args:
            None

        Returns:
            bool: ``True`` for ``role = "peer"``.

        Raises:
            None
        """
        return self.role == PEER

    def header(self) -> str:
        """Describe the node for the TUI header: ``west (peer of https://...)``, or ``""``.

        Args:
            None

        Returns:
            str: The label; empty for a single node with no position.

        Raises:
            None
        """
        if self.is_peer:
            return f"{self.position} (peer of {self.primary_url})"
        return f"{self.position} (primary)" if self.position else ""

    def refusal(self) -> str | None:
        """Say why a metadata edit is refused here, or ``None`` on a primary (MPOS-003).

        Args:
            None

        Returns:
            str | None: The message, naming the primary.

        Raises:
            None
        """
        if not self.is_peer:
            return None
        where = self.primary_url
        return f"this node is a peer; its primary is {where}: change metadata there (MPOS-003)"


def _get(config: EffectiveConfig, key: str) -> Any:
    """Read one ``[node]`` option, tolerating an absent table.

    Args:
        config: The effective configuration.
        key: Dotted key.

    Returns:
        Any: The value, or ``None``.

    Raises:
        None
    """
    try:
        return config.get(key)
    except KeyError:
        return None


def node_settings(config: EffectiveConfig) -> NodeSettings:
    """Build the node settings; call ``node_problems`` first where problems matter.

    Args:
        config: The effective configuration.

    Returns:
        NodeSettings: The settings (``role`` falls back to ``primary``).

    Raises:
        None
    """
    role = str(_get(config, "node.role") or PRIMARY)
    return NodeSettings(
        role=role if role in (PRIMARY, PEER) else PRIMARY,
        position=str(_get(config, "node.position") or "").strip(),
        primary_url=str(_get(config, "node.primary_url") or "").strip().rstrip("/"),
        peer_url=str(_get(config, "node.peer_url") or "").strip().rstrip("/"),
    )


def _bad_url(url: str) -> bool:
    """Tell whether a partner URL is not a plain ``https://`` address.

    Args:
        url: The configured URL.

    Returns:
        bool: ``True`` for another scheme, no host, a user name, or a query.

    Raises:
        None
    """
    parts = urlsplit(url)
    return parts.scheme != "https" or not parts.hostname or bool(parts.username or parts.query)


def node_problems(config: EffectiveConfig) -> list[str]:
    """Check the ``[node]`` table for ``vakr config validate`` and the startup checks.

    Args:
        config: The effective configuration, valid option by option.

    Returns:
        list[str]: One line per problem; empty when consistent.

    Raises:
        None
    """
    node = node_settings(config)
    problems: list[str] = []
    if node.position and not _POSITION.fullmatch(node.position):
        problems.append(f"node.position must be 1 to {MAX_POSITION} letters, digits, '.', '_', "
                        "or '-', starting and ending with a letter or digit")  # fmt: skip
    if node.is_peer:
        if _bad_url(node.primary_url):
            problems.append("node.primary_url must be the primary's https:// address on a peer "
                            "(no user name, query, or plain http; MPOS-006)")  # fmt: skip
        if not node.position:
            problems.append(
                "node.position must be set on a peer, so the positions can be told apart"
            )
        if node.peer_url:
            problems.append("node.peer_url is for the primary; a peer pulls from node.primary_url")
    elif node.primary_url:
        problems.append("node.primary_url is set but node.role is primary; remove it, or set "
                        'node.role = "peer"')  # fmt: skip
    if not node.is_peer and node.peer_url and _bad_url(node.peer_url):
        problems.append("node.peer_url must be the peer's https:// address (no user name, query, "
                        "or plain http; MPOS-006)")  # fmt: skip
    return problems
