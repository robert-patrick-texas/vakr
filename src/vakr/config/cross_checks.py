"""Cross-option rules that ``vakr config validate`` runs after the catalog's own (CONF-009).

The catalog checks each option alone: its type, range, or allowed values.
Some rules need a subsystem's own knowledge or span several options: a
``re:`` keyword that could backtrack catastrophically (POLICY-005), a
lateness threshold above the start delay (E.25), spool thresholds out of
order (E.24). Each subsystem owns its rule; this module lists them, so an
operator finds the problem when validating rather than when a service
refuses to start.

Dependencies:
    vakr.config.keyword_check, vakr.scheduler.settings, vakr.ingest.settings,
    vakr.inventory.settings, vakr.auth.settings: The subsystem rules.

Safety:
    Read-only. Messages name options and values, never secrets.

Example:
    >>> [label for label, _ in RULES][:2]
    ['unsafe or invalid interface keyword', 'inconsistent scheduler settings']
"""

from collections.abc import Callable

from vakr.auth.settings import auth_problems
from vakr.config.keyword_check import keyword_problems
from vakr.config.loader import EffectiveConfig
from vakr.ingest.settings import spool_problems
from vakr.inventory.settings import inventory_problems
from vakr.scheduler.settings import scheduler_problems

RULES: tuple[tuple[str, Callable[[EffectiveConfig], list[str]]], ...] = (
    ("unsafe or invalid interface keyword", keyword_problems),
    ("inconsistent scheduler settings", scheduler_problems),
    ("inconsistent spool settings", spool_problems),
    ("invalid inventory file", inventory_problems),
    ("inconsistent auth settings", auth_problems),
)
"""``(what is wrong, rule)`` pairs, run in order; the first failing rule is reported."""


def first_problem(config: EffectiveConfig) -> tuple[str, list[str]] | None:
    """Run the rules and return the first one that finds problems.

    Args:
        config: The effective configuration, already valid option by option.

    Returns:
        tuple[str, list[str]] | None: What is wrong and one line per
        problem, or ``None`` when every rule passes.

    Raises:
        None
    """
    for label, rule in RULES:
        if problems := rule(config):
            return label, problems
    return None
