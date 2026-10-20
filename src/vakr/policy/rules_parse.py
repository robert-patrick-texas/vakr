"""Parse and validate ``policies.toml`` into ``Policy`` rules (POLICY-001, POLICY-005).

Rules arrive as plain dicts from ``tomllib`` or ``json`` under
``[[interface_policy.rules]]``, the same table name as the section 11.3
example, so a block can move between files unchanged. A rule's scope is
written as ``scope = "device"`` or inferred from its selector keys
(``groups``, ``tags``, ``sites``, ``devices``, or ``devices`` plus
``interfaces``). The section 11.3 shorthand ``exclude_ifname_patterns``
means ``action = "exclude"`` plus ``match.ifname``; it works for
``include_`` and every match field.

Dependencies:
    vakr.policy.reader: Typed reads with dotted-path problems.
    vakr.policy.patterns, vakr.policy.settings: Conditions and actions.

Safety:
    Parsing is pure. Every problem is collected and raised together as
    ``PolicyError``; unknown keys are errors naming their path.

Example:
    >>> p = parse_policy({"interface_policy": {"rules": [
    ...     {"name": "no-loopbacks", "exclude_ifname_patterns": ["Loopback*"]}]}})
    >>> p.rules[0].scope.value, p.rules[0].action.value
    ('global', 'exclude')
"""

import re
from collections.abc import Mapping
from typing import Any

from vakr.policy.errors import PolicyIssue, raise_if_any
from vakr.policy.facts import TYPED_FIELDS, MatchField
from vakr.policy.patterns import Pattern, compile_pattern, compile_patterns
from vakr.policy.reader import TableReader
from vakr.policy.rules import Action, Condition, Policy, Rule, ScopeKind
from vakr.policy.settings import parse_annotations, parse_settings

MAX_RULES = 2000
"""Most rules one policy may hold, keeping per-interface evaluation bounded."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

# Scope selector keys and the fact field each one tests, in condition order.
_SELECTORS: tuple[tuple[str, MatchField], ...] = (
    ("devices", MatchField.DEVICE),
    ("interfaces", MatchField.IFNAME),
    ("groups", MatchField.DEVICE_GROUPS),
    ("tags", MatchField.TAGS),
    ("sites", MatchField.SITE),
)
_SCOPE_KEYS: Mapping[ScopeKind, frozenset[str]] = {
    ScopeKind.GLOBAL: frozenset(),
    ScopeKind.GROUP: frozenset({"groups"}),
    ScopeKind.TAG: frozenset({"tags"}),
    ScopeKind.SITE: frozenset({"sites"}),
    ScopeKind.DEVICE: frozenset({"devices"}),
    ScopeKind.INTERFACE: frozenset({"devices", "interfaces"}),
}
_SHORTHANDS = {f"{a.value}_{f.value}_patterns": (a, f) for a in Action for f in MatchField}
_RULE_KEYS = frozenset(
    {"name", "description", "scope", "priority", "action", "match", "settings", "annotations"}
    | {key for key, _field in _SELECTORS}
    | set(_SHORTHANDS)
)


def _condition(
    field: MatchField, value: object, path: str, issues: list[PolicyIssue]
) -> tuple[Pattern, ...]:
    """Compile one condition's values; typed fields take fixed values, not patterns.

    Args:
        field: Fact field.
        value: Patterns (or ``true``/``false``, or status names) as loaded.
        path: Dotted location.
        issues: List that problems are appended to.

    Returns:
        tuple[Pattern, ...]: The compiled patterns; empty when invalid.

    Raises:
        None
    """
    allowed = TYPED_FIELDS.get(field)
    if allowed is None:
        return compile_patterns(value, path, issues)
    values = value if isinstance(value, list) else [value]
    names = [str(v).lower() if isinstance(v, bool) else v for v in values]
    if not names or any(name not in allowed for name in names):
        issues.append(PolicyIssue(path, f"expected a list of: {', '.join(allowed)}"))
        return ()
    return tuple(compile_pattern(f"exact:{name}", path) for name in names)


def _scope(reader: TableReader) -> ScopeKind | None:
    """Determine the rule's scope from ``scope`` or from its selector keys.

    Args:
        reader: Reader over the rule table.

    Returns:
        ScopeKind | None: The scope, or ``None`` when it is invalid.

    Raises:
        None
    """
    present = {key for key, _field in _SELECTORS if key in reader.data}
    written = reader.choice("scope", [s.value for s in ScopeKind])
    if written is not None:
        scope = ScopeKind(written)
    else:
        inferred = [kind for kind, keys in _SCOPE_KEYS.items() if keys == present]
        if not inferred:
            reader.problem(
                "",
                f"{', '.join(sorted(present))} do not make one scope; use one of groups, tags, "
                "sites, or devices (plus interfaces for an override) and put the rest in [match]",
            )
            return None
        scope = inferred[0]
    if present != _SCOPE_KEYS[scope]:
        needed = ", ".join(sorted(_SCOPE_KEYS[scope])) or "no selector keys"
        reader.problem("scope", f'scope "{scope.value}" takes {needed}')
        return None
    return scope


def _conditions(
    reader: TableReader, issues: list[PolicyIssue]
) -> tuple[tuple[Condition, ...], Action | None]:
    """Collect scope selectors, ``match`` entries, and any shorthand key.

    Args:
        reader: Reader over the rule table.
        issues: List that problems are appended to.

    Returns:
        tuple[tuple[Condition, ...], Action | None]: Conditions in canonical
        order and the action implied by a shorthand key.

    Raises:
        None
    """
    found = [
        Condition(field, compile_patterns(reader.data[key], reader.at(key), issues), key)
        for key, field in _SELECTORS
        if key in reader.data
    ]
    fields = [f.value for f in MatchField]
    match_table = reader.data.get("match", {})
    match = dict(TableReader(match_table, reader.at("match"), issues, fields).data)
    shorthand = sorted(key for key in _SHORTHANDS if key in reader.data)
    implied = None
    if shorthand:
        implied, field = _SHORTHANDS[shorthand[0]]
        if len(shorthand) > 1 or "action" in reader.data or field.value in match:
            reader.problem(shorthand[0], "use one shorthand key, or action with [match], not both")
        match[field.value] = reader.data[shorthand[0]]
    for field in MatchField:
        if field.value in match:
            path = reader.at(f"match.{field.value}")
            found.append(Condition(field, _condition(field, match[field.value], path, issues)))
    return tuple(found), implied


def _rule(table: object, index: int, issues: list[PolicyIssue]) -> Rule | None:
    """Parse one rule table.

    Args:
        table: The rule as loaded.
        index: 0-based position in the rules list.
        issues: List that problems are appended to.

    Returns:
        Rule | None: The rule, or ``None`` when it has problems.

    Raises:
        None
    """
    before = len(issues)
    reader = TableReader(table, f"interface_policy.rules[{index}]", issues, _RULE_KEYS)
    name = reader.text("name", required=True) or ""
    if name and not _NAME.fullmatch(name):
        reader.problem("name", "use 1-64 letters, digits, '.', '_' or '-', starting alphanumeric")
    scope = _scope(reader)
    conditions, implied = _conditions(reader, issues)
    written = reader.choice("action", [a.value for a in Action])
    action = Action(written) if written else implied
    settings = parse_settings(reader.data.get("settings", {}), reader.at("settings"), issues)
    annotations = parse_annotations(
        reader.data.get("annotations", {}), reader.at("annotations"), issues
    )
    # Only when everything else parsed: an invalid action is already reported.
    if len(issues) == before and action is None and not settings and not annotations.items():
        reader.problem("", "rule does nothing: set action, settings, or annotations")
    priority = reader.integer("priority", -1000, 1000) or 0
    description = reader.text("description") or ""
    if len(issues) > before or scope is None:
        return None
    return Rule(
        name, index + 1, scope, priority, conditions, action, settings, annotations, description
    )


def parse_policy(data: Mapping[str, Any], source: str = "") -> Policy:
    """Validate a policies document as loaded from TOML or JSON.

    Args:
        data: Top-level table; rules live under ``interface_policy.rules``.
        source: File name for display.

    Returns:
        Policy: The validated rules in file order.

    Raises:
        PolicyError: Listing every problem, each with its dotted path.
    """
    issues: list[PolicyIssue] = []
    top = TableReader(data, "", issues, {"interface_policy"})
    section_table = top.data.get("interface_policy", {})
    section = TableReader(section_table, "interface_policy", issues, {"rules"})
    rules_value = section.data.get("rules", [])
    if not isinstance(rules_value, list) or len(rules_value) > MAX_RULES:
        section.problem("rules", f"expected a list of at most {MAX_RULES} rule tables")
        rules_value = []
    rules = [rule for i, t in enumerate(rules_value) if (rule := _rule(t, i, issues))]
    seen: dict[str, int] = {}
    for rule in rules:
        if rule.name in seen:
            path = f"interface_policy.rules[{rule.position - 1}].name"
            issues.append(PolicyIssue(path, f"duplicate rule name (also rule #{seen[rule.name]})"))
        seen.setdefault(rule.name, rule.position)
    raise_if_any(issues)
    return Policy(tuple(rules), source)
