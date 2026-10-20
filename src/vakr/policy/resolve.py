"""Resolve each effective value from its competing offers (POLICY-002, META-012).

Every value an interface ends up with has one winner among several offers,
highest precedence first:

- interest, sticky, and WAN circuit: explicit vakr annotation, then
  description keyword, then the highest-precedence matching rule, then the
  default (``secondary``, not sticky, not WAN; META-011, META-012);
- monitoring: the highest-precedence matching rule with an action, then the
  default, which is to monitor;
- settings: the highest-precedence matching rule that sets them; otherwise
  the configured default applies and no value is recorded.

The trace keeps the losers too, so explain output can say what was
overridden and why.

Dependencies:
    vakr.policy.trace: Candidate and value records.

Safety:
    Pure and deterministic: offers are ranked by an explicit order, never
    by dictionary or set iteration.

Example:
    >>> resolve("sticky", [], []).winner.source.value
    'default'
"""

from typing import cast

from vakr.domain.enums import InterestClass
from vakr.policy.classify import Classification
from vakr.policy.facts import InterfaceFacts
from vakr.policy.rules import Action, Rule
from vakr.policy.trace import Candidate, Source, TraceValue, ValueTrace

ANNOTATION_VALUES = ("interest", "sticky", "wan_circuit")
"""Values resolved with the META-012 precedence chain."""

DEFAULTS: dict[str, TraceValue] = {
    "monitor": Action.INCLUDE,
    "interest": InterestClass.SECONDARY,
    "sticky": False,
    "wan_circuit": False,
}
"""Values that apply when nothing else offers one."""

_UNSET = "no annotation, keyword, or rule sets it"
_DEFAULT_REASONS = {
    "monitor": "no matching rule includes or excludes it; interfaces are monitored by default",
    "interest": f"{_UNSET}; interest defaults to secondary (META-011)",
    "sticky": _UNSET,
    "wan_circuit": _UNSET,
}
_ANNOTATION_REASON = "explicit vakr annotation; outranks description keywords and rules (META-012)"


def rule_reason(rule: Rule) -> str:
    """Explain why a rule's offer won.

    Args:
        rule: The winning rule.

    Returns:
        str: Its tier, priority, and position.

    Raises:
        None
    """
    where = f"{rule.tier.name.lower()} tier, priority {rule.priority}, #{rule.position}"
    return f"highest-precedence matching rule ({where})"


def higher_offers(
    name: str, facts: InterfaceFacts, classification: Classification
) -> list[tuple[Candidate, str]]:
    """Return annotation and keyword offers, which outrank rules (META-012).

    Args:
        name: Value name.
        facts: The interface.
        classification: Its description keywords.

    Returns:
        list[tuple[Candidate, str]]: Offers with their reasons, highest first;
        empty for values other than interest, sticky, and WAN circuit.

    Raises:
        None
    """
    if name not in ANNOTATION_VALUES:
        return []
    found: list[tuple[Candidate, str]] = []
    explicit = getattr(facts.annotations, name)
    if explicit is not None:
        found.append((Candidate(Source.ANNOTATION, explicit, "annotation"), _ANNOTATION_REASON))
    hit = classification.interest if name == "interest" else classification.flag(name)
    if hit is not None:
        value = cast("TraceValue", hit.keyword.interest if name == "interest" else True)
        reason = f'description keyword {hit.keyword.text} matched "{hit.token}"; outranks rules'
        found.append((Candidate(Source.KEYWORD, value, hit.keyword.text), reason))
    return found


def resolve(
    name: str, higher: list[tuple[Candidate, str]], offers: list[tuple[Rule, TraceValue]]
) -> ValueTrace | None:
    """Pick the winning offer for one value.

    Args:
        name: Value name.
        higher: Annotation and keyword offers, highest first.
        offers: Matching rules' offers in ascending precedence.

    Returns:
        ValueTrace | None: The winner and what it overrode, or ``None`` when
        nothing offers a value (a setting no rule changes).

    Raises:
        None
    """
    ranked = list(higher)
    ranked += [(Candidate(Source.RULE, v, r.name), rule_reason(r)) for r, v in reversed(offers)]
    if name in DEFAULTS:
        default = Candidate(Source.DEFAULT, DEFAULTS[name], "default")
        ranked.append((default, _DEFAULT_REASONS[name]))
    if not ranked:
        return None
    (winner, reason), rest = ranked[0], ranked[1:]
    return ValueTrace(name, winner, reason, tuple(candidate for candidate, _ in rest))
