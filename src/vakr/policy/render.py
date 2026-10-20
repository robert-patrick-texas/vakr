"""Render explain traces as stable text or JSON (POLICY-004, POLICY-006, CFG-009).

``vakr policy explain`` prints this for NetOps; the JSON form is for
scripts and for the API that arrives with the web UI. Both list rules in
the order they were applied, lowest precedence first, so reading top to
bottom follows the evaluation: a later matching rule overrides an earlier
one. Output depends only on the explanation, so the same input always
prints the same bytes.

Dependencies:
    vakr.policy.trace: The explanation records.

Safety:
    Prints inventory facts and rule names only; policies hold no secrets.

Example:
    >>> from vakr.policy.facts import MatchField
    >>> describe_check(ConditionCheck(MatchField.IFNAME, ("Vlan*",), ("Vlan100",), True, "Vlan*",
    ...     "Vlan100"))
    'match ifname: "Vlan100" matches Vlan*'
"""

from typing import Any

from vakr.policy.settings import format_setting
from vakr.policy.trace import (
    Candidate,
    ConditionCheck,
    Explanation,
    RuleTrace,
    TraceValue,
    ValueTrace,
)


def _plain(value: TraceValue) -> bool | int | float | str:
    """Convert a value to its JSON form; enums become their stored strings.

    Args:
        value: Any traced value.

    Returns:
        bool | int | float | str: JSON-ready value; durations stay seconds.

    Raises:
        None
    """
    return value if isinstance(value, bool | int | float) else str(value.value)


def describe_check(check: ConditionCheck) -> str:
    """Describe one condition result in a line.

    Args:
        check: The condition result.

    Returns:
        str: Such as ``scope groups: no value matches access-switches``.

    Raises:
        None
    """
    label = f"scope {check.selector}" if check.selector else f"match {check.field.value}"
    if check.matched:
        return f'{label}: "{check.value}" matches {check.pattern}'
    shown = ", ".join(f'"{v}"' for v in check.values) or "(none)"
    note = f"; {check.note}" if check.note else ""
    return f"{label}: {shown} matches none of {', '.join(check.patterns)}{note}"


def _candidate(name: str, candidate: Candidate) -> str:
    """Describe one offer as ``value from source origin``.

    Args:
        name: Value name, for formatting.
        candidate: The offer.

    Returns:
        str: Such as ``exclude from rule no-svis``.

    Raises:
        None
    """
    shown = format_setting(name, candidate.value)
    if candidate.source.value in {"annotation", "default"}:
        return f"{shown} ({candidate.source.value})"
    return f"{shown} from {candidate.source.value} {candidate.origin}"


def _rule_lines(trace: RuleTrace) -> list[str]:
    """Render one rule's outcome.

    Args:
        trace: The rule's trace.

    Returns:
        list[str]: Header line, then indented detail lines.

    Raises:
        None
    """
    rule = trace.rule
    tier = rule.tier.name.lower()
    scope = "" if rule.scope.value == tier else f"{rule.scope.value} scope, "
    where = f"{scope}{tier} tier, priority {rule.priority}"
    head = f"  #{rule.position} {rule.name} [{where}]: {'matched' if trace.matched else 'no match'}"
    deciding = trace.deciding
    if deciding is not None:
        return [head, f"      decided by {describe_check(deciding)}"]
    lines = [head, *(f"      {describe_check(check)}" for check in trace.checks)]
    lost = [name for name in trace.offers if name not in trace.won]
    if trace.won:
        lines.append(f"      sets: {', '.join(trace.won)}")
    if lost:
        lines.append(f"      overridden: {', '.join(lost)}")
    return lines


def render_text(explanation: Explanation) -> str:
    """Render an explanation for a terminal.

    Args:
        explanation: The explanation.

    Returns:
        str: Multi-line text ending in a newline.

    Raises:
        None
    """
    facts, decision, found = explanation.facts, explanation.decision, explanation.classification
    state = "monitored" if decision.monitored else "suppressed (not monitored; still inventoried)"
    by = f"rule {decision.effective_rule}" if decision.effective_rule else "default"
    index = f" (ifIndex {facts.if_index})" if facts.if_index is not None else ""
    lines = [f"interface {facts.label}{index}", f"  decision: {state} [{by}]"]
    description = "(unknown)" if found.description is None else f'"{found.description}"'
    lines.append(f"  description: {description}")
    lines.append(f"    tokens: {' '.join(found.tokens) or '(none)'}")
    hits = [f"{h.keyword.text} ({h.token}) -> {h.keyword.tag}" for h in found.hits]
    lines.append(f"    keywords: {'; '.join(hits) or '(none)'}")
    lines.append(f"  description tags: {', '.join(decision.description_tags) or '(none)'}")
    lines.append(f"  operator tags: {', '.join(decision.operator_tags) or '(none)'}")
    lines.append(f"  not yet classified: {'yes' if explanation.not_yet_classified else 'no'}")
    source = f" from {explanation.policy_source}" if explanation.policy_source else ""
    lines.append(f"rules{source}, lowest precedence first (a later match overrides):")
    for trace in explanation.rules:
        lines.extend(_rule_lines(trace))
    if not explanation.rules:
        lines.append("  (no rules)")
    lines.append("values:")
    for value in explanation.values:
        lines.append(f"  {value.name} = {_candidate(value.name, value.winner)}")
        lines.append(f"      why: {value.reason}")
        for other in value.overridden:
            lines.append(f"      overrides: {_candidate(value.name, other)}")
    return "\n".join(lines) + "\n"


def _check_dict(check: ConditionCheck) -> dict[str, Any]:
    """Convert a condition result to JSON-ready data.

    Args:
        check: The condition result.

    Returns:
        dict[str, Any]: Its fields with stable keys.

    Raises:
        None
    """
    return {
        "field": check.field.value,
        "selector": check.selector or None,
        "patterns": list(check.patterns),
        "values": list(check.values),
        "matched": check.matched,
        "pattern": check.pattern,
        "value": check.value,
        "note": check.note,
    }


def _rule_dict(trace: RuleTrace) -> dict[str, Any]:
    """Convert one rule's outcome to JSON-ready data.

    Args:
        trace: The rule's trace.

    Returns:
        dict[str, Any]: Rule identity, outcome, every condition, and offers.

    Raises:
        None
    """
    rule = trace.rule
    return {
        "position": rule.position,
        "name": rule.name,
        "scope": rule.scope.value,
        "tier": rule.tier.name.lower(),
        "priority": rule.priority,
        "matched": trace.matched,
        "decided_by": _check_dict(trace.deciding) if trace.deciding else None,
        "conditions": [_check_dict(check) for check in trace.checks],
        "offers": list(trace.offers),
        "won": list(trace.won),
    }


def _value_dict(value: ValueTrace) -> dict[str, Any]:
    """Convert one effective value and its competition to JSON-ready data.

    Args:
        value: The value's trace.

    Returns:
        dict[str, Any]: Winner, reason, and the offers it overrode.

    Raises:
        None
    """
    winner = value.winner
    return {
        "name": value.name,
        "value": _plain(winner.value),
        "display": format_setting(value.name, winner.value),
        "source": winner.source.value,
        "origin": winner.origin,
        "reason": value.reason,
        "overridden": [
            {"value": _plain(c.value), "source": c.source.value, "origin": c.origin}
            for c in value.overridden
        ],
    }


def _decision_dict(explanation: Explanation) -> dict[str, Any]:
    """Convert the decision to JSON-ready data.

    Args:
        explanation: The explanation holding the decision.

    Returns:
        dict[str, Any]: Monitoring, annotations, settings, and tags.

    Raises:
        None
    """
    decision = explanation.decision
    return {
        "monitored": decision.monitored,
        "inventoried": decision.inventoried,
        "effective_rule": decision.effective_rule,
        "interest": decision.interest.value,
        "sticky": decision.sticky,
        "wan_circuit": decision.wan_circuit,
        "settings": {name: _plain(value) for name, value in decision.settings},
        "description_tags": list(decision.description_tags),
        "operator_tags": list(decision.operator_tags),
        "not_yet_classified": explanation.not_yet_classified,
    }


def to_dict(explanation: Explanation) -> dict[str, Any]:
    """Convert an explanation to JSON-ready data with a stable key order.

    Args:
        explanation: The explanation.

    Returns:
        dict[str, Any]: ``interface``, ``decision``, ``classification``,
        ``rules``, and ``values``.

    Raises:
        None
    """
    facts, found = explanation.facts, explanation.classification
    keywords = [
        {"keyword": h.keyword.text, "token": h.token, "tag": h.keyword.tag} for h in found.hits
    ]
    return {
        "interface": {
            "device": facts.device_name,
            "ifname": facts.if_name,
            "ifindex": facts.if_index,
            "ifalias": facts.if_alias,
        },
        "decision": _decision_dict(explanation),
        "classification": {
            "description": found.description,
            "tokens": list(found.tokens),
            "too_long": found.too_long,
            "keywords": keywords,
        },
        "rules": [_rule_dict(trace) for trace in explanation.rules],
        "values": [_value_dict(value) for value in explanation.values],
    }
