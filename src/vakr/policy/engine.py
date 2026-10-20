"""The policy engine: one deterministic decision per interface, with its trace.

This is the single implementation of policy evaluation (blueprint 17.1);
the scheduler, UI, API, and CLI all call it. For one interface it:

1. classifies the description into keyword tags (META-012);
2. applies every rule in ascending precedence (global, group/tag/site,
   device, interface override, then priority, then file order;
   POLICY-002), testing every condition so the trace is complete;
3. resolves each value from its highest-precedence offer (``resolve``).

The same facts, rules, and keywords always give the same decision and the
same trace (POLICY-006): no clock, randomness, or set iteration is involved.

Dependencies:
    vakr.policy.classify, vakr.policy.rules: Keywords and rules.
    vakr.policy.resolve, vakr.policy.trace: Precedence and trace records.

Safety:
    Pure. An excluded interface is reported as not monitored and still
    inventoried, never as absent (DISC-007).

Example:
    >>> from vakr.policy.keywords import KeywordSet
    >>> from vakr.policy.rules import Policy
    >>> facts = InterfaceFacts("edge-rtr1", if_name="Loopback0")
    >>> decide(facts, Policy(), KeywordSet()).monitored
    True
"""

from dataclasses import replace
from typing import cast

from vakr.domain.enums import InterestClass
from vakr.policy.classify import Classification, classify, not_yet_classified
from vakr.policy.facts import MULTI_VALUED, InterfaceFacts, field_values
from vakr.policy.keywords import KeywordSet
from vakr.policy.patterns import MAX_INPUT_LENGTH, PatternKind
from vakr.policy.resolve import ANNOTATION_VALUES, higher_offers, resolve
from vakr.policy.rules import Action, Condition, Policy, Rule
from vakr.policy.settings import SETTING_LIMITS, SettingValue
from vakr.policy.trace import (
    ConditionCheck,
    Decision,
    Explanation,
    RuleTrace,
    Source,
    TraceValue,
    ValueTrace,
)

type Offers = dict[str, list[tuple[Rule, TraceValue]]]


def check_condition(
    condition: Condition, facts: InterfaceFacts, description_tags: tuple[str, ...]
) -> ConditionCheck:
    """Test one condition: does any fact value match any of its patterns?

    Values are tried in fact order and patterns in rule order, so the
    reported match is deterministic.

    Args:
        condition: The condition.
        facts: The interface.
        description_tags: Tags from description keywords.

    Returns:
        ConditionCheck: The outcome, with the matching pattern and value.

    Raises:
        None
    """
    values = field_values(facts, condition.field, description_tags)
    texts = tuple(p.text for p in condition.patterns)
    for value in values:
        for pattern in condition.patterns:
            if pattern.matches(value):
                return ConditionCheck(
                    condition.field, texts, values, True, pattern.text, value, condition.selector
                )
    unknown = not values and condition.field not in MULTI_VALUED
    note = "value unknown" if unknown else ""
    long_values = any(len(v) > MAX_INPUT_LENGTH for v in values)
    if long_values and any(p.kind is not PatternKind.EXACT for p in condition.patterns):
        note = f"values over {MAX_INPUT_LENGTH} characters never match glob or re: patterns"
    return ConditionCheck(
        condition.field, texts, values, False, selector=condition.selector, note=note
    )


def _offers(rule: Rule) -> tuple[tuple[str, TraceValue], ...]:
    """List the values a rule sets, monitoring first.

    Args:
        rule: The rule.

    Returns:
        tuple[tuple[str, TraceValue], ...]: ``(name, value)`` pairs.

    Raises:
        None
    """
    head: tuple[tuple[str, TraceValue], ...] = (("monitor", rule.action),) if rule.action else ()
    return head + rule.settings + rule.annotations.items()


def _decision(
    facts: InterfaceFacts, classification: Classification, values: dict[str, ValueTrace]
) -> Decision:
    """Assemble the decision from the resolved values.

    Args:
        facts: The interface.
        classification: Its description keywords.
        values: Resolved values by name.

    Returns:
        Decision: The effective outcome.

    Raises:
        None
    """
    monitor = values["monitor"].winner
    settings = tuple(
        (name, cast("SettingValue", values[name].winner.value))
        for name in SETTING_LIMITS
        if name in values
    )
    return Decision(
        monitored=monitor.value is Action.INCLUDE,
        interest=InterestClass(values["interest"].winner.value),
        sticky=bool(values["sticky"].winner.value),
        wan_circuit=bool(values["wan_circuit"].winner.value),
        settings=settings,
        description_tags=classification.tags,
        operator_tags=facts.annotations.tags,
        effective_rule=monitor.origin if monitor.source is Source.RULE else None,
    )


def _outcome(trace: RuleTrace, values: dict[str, ValueTrace]) -> RuleTrace:
    """Record which of a matching rule's offers took effect.

    Args:
        trace: The rule's trace.
        values: Resolved values by name.

    Returns:
        RuleTrace: The trace with ``offers`` and ``won`` filled in.

    Raises:
        None
    """
    if not trace.matched:
        return trace
    offers = tuple(name for name, _value in _offers(trace.rule))
    won = tuple(
        name
        for name in offers
        if values[name].winner.source is Source.RULE
        and values[name].winner.origin == trace.rule.name
    )
    return replace(trace, offers=offers, won=won)


def explain(facts: InterfaceFacts, policy: Policy, keywords: KeywordSet) -> Explanation:
    """Evaluate the policy for one interface and record why (POLICY-004, CFG-009).

    Args:
        facts: The interface.
        policy: Validated rules.
        keywords: Effective description keywords.

    Returns:
        Explanation: Decision, every rule's outcome in the order applied,
        and every value's winning source.

    Raises:
        None
    """
    classification = classify(facts.if_alias, keywords)
    traces: list[RuleTrace] = []
    offers: Offers = {}
    for rule in sorted(policy.rules, key=lambda r: r.precedence):
        checks = tuple(check_condition(c, facts, classification.tags) for c in rule.conditions)
        matched = all(check.matched for check in checks)
        traces.append(RuleTrace(rule, matched, checks))
        for name, value in _offers(rule) if matched else ():
            offers.setdefault(name, []).append((rule, value))
    names = ["monitor", *(n for n in SETTING_LIMITS if n in offers), *ANNOTATION_VALUES]
    resolved = (
        resolve(n, higher_offers(n, facts, classification), offers.get(n, [])) for n in names
    )
    values = {value.name: value for value in resolved if value is not None}
    return Explanation(
        facts=facts,
        decision=_decision(facts, classification, values),
        classification=classification,
        rules=tuple(_outcome(trace, values) for trace in traces),
        values=tuple(values.values()),
        not_yet_classified=not_yet_classified(facts, keywords),
        policy_source=policy.source,
    )


def decide(facts: InterfaceFacts, policy: Policy, keywords: KeywordSet) -> Decision:
    """Return only the effective decision for one interface.

    Args:
        facts: The interface.
        policy: Validated rules.
        keywords: Effective description keywords.

    Returns:
        Decision: The same decision ``explain`` reports.

    Raises:
        None
    """
    return explain(facts, policy, keywords).decision
