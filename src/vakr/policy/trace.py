"""Decisions and explain traces produced by the policy engine (POLICY-004, POLICY-006).

An operator asking "why is ``Vlan100`` not graphed?" needs more than a
yes/no. ``Explanation`` records every rule considered, in the order it was
applied, whether it matched and which condition decided that, and for every
effective value which source won and which offers it overrode. The UI, API,
TUI, and ``vakr policy explain`` all render this one structure, so they
cannot disagree (blueprint 17.1, DRY).

Dependencies:
    vakr.policy.classify, vakr.policy.facts, vakr.policy.rules: The inputs
        the trace refers to.

Safety:
    Immutable value objects. ``Decision.inventoried`` is always ``True``:
    suppression stops collection, never inventory (DISC-007).

Example:
    >>> Candidate(Source.DEFAULT, Action.INCLUDE, "default").source.value
    'default'
"""

from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.enums import InterestClass
from vakr.policy.classify import Classification
from vakr.policy.facts import InterfaceFacts, MatchField
from vakr.policy.rules import Action, Rule
from vakr.policy.settings import SettingValue

type TraceValue = SettingValue | InterestClass | Action


class Source(StrEnum):
    """Where an effective value came from, highest precedence first (META-012)."""

    ANNOTATION = "annotation"
    KEYWORD = "keyword"
    RULE = "rule"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class ConditionCheck:
    """Result of testing one condition of one rule.

    Attributes:
        field: Fact field tested.
        patterns: The condition's patterns as written.
        values: The fact values tested; empty when unknown.
        matched: Whether any value matched any pattern.
        pattern: The pattern that matched, if any.
        value: The value it matched, if any.
        selector: Scope selector key such as ``groups``, or ``""`` for ``match``.
        note: Why nothing could match, when that is not obvious.
    """

    field: MatchField
    patterns: tuple[str, ...]
    values: tuple[str, ...]
    matched: bool
    pattern: str | None = None
    value: str | None = None
    selector: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class RuleTrace:
    """How one rule fared for one interface.

    Attributes:
        rule: The rule.
        matched: Whether every condition matched.
        checks: Every condition's result, in the rule's condition order.
        offers: Names of the values a matching rule tried to set.
        won: Of those, the values that took effect.
    """

    rule: Rule
    matched: bool
    checks: tuple[ConditionCheck, ...]
    offers: tuple[str, ...] = ()
    won: tuple[str, ...] = ()

    @property
    def deciding(self) -> ConditionCheck | None:
        """The first condition that failed, which decided a non-match.

        Args:
            None

        Returns:
            ConditionCheck | None: That check, or ``None`` when the rule matched.

        Raises:
            None
        """
        return next((check for check in self.checks if not check.matched), None)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One offer of a value from one source.

    Attributes:
        source: Annotation, keyword, rule, or default.
        value: The offered value.
        origin: Rule name, keyword text, ``annotation``, or ``default``.
    """

    source: Source
    value: TraceValue
    origin: str


@dataclass(frozen=True, slots=True)
class ValueTrace:
    """Which offer won for one effective value, and why.

    Attributes:
        name: ``monitor``, a setting name, ``interest``, ``sticky``, or ``wan_circuit``.
        winner: The offer that took effect.
        reason: Operator-readable explanation of why it won.
        overridden: Lower-precedence offers it beat, highest first.
    """

    name: str
    winner: Candidate
    reason: str
    overridden: tuple[Candidate, ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    """The effective policy outcome for one interface.

    Attributes:
        monitored: Whether performance collection runs.
        interest: Effective interest class (META-011).
        sticky: Effective sticky flag (LIFE-007).
        wan_circuit: Effective WAN-circuit flag.
        settings: Settings rules changed; others keep configured defaults.
        description_tags: Tags from description keywords (META-012).
        operator_tags: Tags added in vakr (META-013).
        effective_rule: The rule that decided monitoring, or ``None`` for the default.
        inventoried: Always ``True``; suppressed interfaces stay in inventory.
    """

    monitored: bool
    interest: InterestClass
    sticky: bool
    wan_circuit: bool
    settings: tuple[tuple[str, SettingValue], ...]
    description_tags: tuple[str, ...]
    operator_tags: tuple[str, ...]
    effective_rule: str | None
    inventoried: bool = True


@dataclass(frozen=True, slots=True)
class Explanation:
    """The full, ordered account of one policy evaluation (POLICY-004).

    Attributes:
        facts: The interface evaluated.
        decision: The outcome.
        classification: What its description said.
        rules: Every rule, in the order applied (lowest precedence first).
        values: Every effective value with its winning source.
        not_yet_classified: No vakr annotation and no keyword match (META-013).
        policy_source: Where the rules came from.
    """

    facts: InterfaceFacts
    decision: Decision
    classification: Classification
    rules: tuple[RuleTrace, ...]
    values: tuple[ValueTrace, ...]
    not_yet_classified: bool
    policy_source: str = ""
