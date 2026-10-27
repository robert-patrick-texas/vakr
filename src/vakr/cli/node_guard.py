"""Refuse CLI metadata edits on a peer, naming the primary (MPOS-003, MPOS-T03).

A peer's metadata is a copy of its primary's, replaced at every pull, so a
local edit would be lost and would diverge meanwhile. ``refused_on_peer``
wraps the handlers that write replicated metadata (``vakr user`` changes,
``vakr inventory apply``, ``vakr device add|set|remove``, ``vakr interface
claim|annotate|tag``, identity-review decisions, and manual rediscovery);
it runs inside ``guarded``, which turns the refusal into ``vakr: <message>``
and exit status 1.

Dependencies:
    vakr.config: The effective configuration and the node rules.

Safety:
    Reads the configuration only; changes nothing.

Example:
    >>> refused_on_peer.__name__
    'refused_on_peer'
"""

import argparse
import functools
from collections.abc import Callable

from vakr.config.loader import ConfigError, load_effective_config
from vakr.config.node import node_settings
from vakr.paths import InstallPaths

type Handler = Callable[[argparse.Namespace], int]


def peer_refusal() -> str | None:
    """Say why metadata edits are refused on this node, or ``None`` on a primary.

    Args:
        None

    Returns:
        str | None: The refusal, naming the primary.

    Raises:
        None
    """
    try:
        config = load_effective_config(InstallPaths.from_environment().config_dir)
    except ConfigError:
        return None  # an unreadable configuration is reported by the command itself
    return node_settings(config).refusal()


def refused_on_peer(run: Handler) -> Handler:
    """Wrap a handler so it refuses on a peer before it touches metadata.

    Args:
        run: The handler.

    Returns:
        Handler: The wrapped handler.

    Raises:
        None
    """

    @functools.wraps(run)
    def handler(args: argparse.Namespace) -> int:
        """Refuse on a peer, else run.

        Args:
            args: Parsed arguments.

        Returns:
            int: Exit status.

        Raises:
            ValueError: On a peer; ``guarded`` prints it.
        """
        if (refusal := peer_refusal()) is not None:
            raise ValueError(refusal)
        return run(args)

    return handler
