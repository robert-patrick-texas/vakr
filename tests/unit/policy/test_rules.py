"""POLICY-001/002/007: parsing ``[[interface_policy.rules]]`` with scopes, actions, and limits.

Dependencies:
    pytest: Parametrised cases.
    vakr.policy.rules_parse: The parser under test.

Example:
    $ uv run pytest tests/unit/policy/test_rules.py
"""

from typing import Any

import pytest

from tests.unit.policy.policy_fixtures import rules
from vakr.domain.enums import InterestClass, ReachabilityStrategy
from vakr.policy.errors import PolicyError, PolicyIssue
from vakr.policy.facts import MatchField
from vakr.policy.reader import TableReader
from vakr.policy.rules import Action, ScopeKind, Tier
from vakr.policy.rules_parse import MAX_RULES, parse_policy
from vakr.policy.settings import format_seconds, format_setting


def _problems(*tables: dict[str, Any]) -> list[str]:
    """Parse rules that must fail and return the formatted problems.

    Args:
        *tables: Rule tables.

    Returns:
        list[str]: ``path: reason`` lines.

    Raises:
        AssertionError: If the rules parse.
    """
    with pytest.raises(PolicyError) as caught:
        rules(*tables)
    return [str(issue) for issue in caught.value.issues]


def test_section_11_3_example_parses_with_its_shorthand() -> None:
    """The blueprint's access-switch rule parses as group scope plus exclude on ``ifname``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules(
        {
            "name": "access-switch-uninteresting",
            "groups": ["access-switches"],
            "exclude_ifname_patterns": ["Loopback*", "Vlan*", "BVI*", "Tunnel*", "Null*"],
        }
    )
    (rule,) = policy.rules
    assert (rule.scope, rule.tier, rule.action) == (ScopeKind.GROUP, Tier.GROUP, Action.EXCLUDE)
    assert [(c.field, c.selector) for c in rule.conditions] == [
        (MatchField.DEVICE_GROUPS, "groups"),
        (MatchField.IFNAME, ""),
    ]
    assert [p.text for p in rule.conditions[1].patterns][:2] == ["Loopback*", "Vlan*"]


@pytest.mark.parametrize(
    ("selectors", "scope", "tier"),
    [
        ({}, ScopeKind.GLOBAL, Tier.GLOBAL),
        ({"groups": ["access-switches"]}, ScopeKind.GROUP, Tier.GROUP),
        ({"tags": ["wan"]}, ScopeKind.TAG, Tier.GROUP),
        ({"sites": ["nyc01"]}, ScopeKind.SITE, Tier.GROUP),
        ({"devices": ["edge-rtr1"]}, ScopeKind.DEVICE, Tier.DEVICE),
        (
            {"devices": ["edge-rtr1"], "interfaces": ["Gi0/0/0"]},
            ScopeKind.INTERFACE,
            Tier.INTERFACE,
        ),
    ],
)
def test_scope_is_inferred_from_selectors(
    selectors: dict[str, list[str]], scope: ScopeKind, tier: Tier
) -> None:
    """POLICY-002: each selector key implies one scope; site shares the group/tag tier.

    Args:
        selectors: Selector keys of the rule.
        scope: Expected scope.
        tier: Expected tier.

    Returns:
        None

    Raises:
        None
    """
    (rule,) = rules({"name": "r", "action": "include", **selectors}).rules
    assert (rule.scope, rule.tier) == (scope, tier)
    explicit = rules({"name": "r", "action": "include", "scope": scope.value, **selectors})
    assert explicit.rules[0].scope is scope


def test_full_rule_with_settings_annotations_and_typed_fields() -> None:
    """POLICY-007: settings within limits, annotations, and typed match fields parse.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    (rule,) = rules(
        {
            "name": "wan-circuits",
            "description": "WAN circuits are polled closely",
            "scope": "tag",
            "tags": ["wan"],
            "priority": 10,
            "match": {"physical": True, "oper_status": ["up", "lower_layer_down"]},
            "settings": {
                "polling_interval": "30s",
                "discovery_interval": "12h",
                "reachability_strategy": "snmp_fallback",
                "snmp_timeout": "5s",
                "snmp_retries": 2,
                "adaptive_polling": True,
                "stale_after": "30d",
            },
            "annotations": {"high_priority": True, "sticky": True, "wan_circuit": True},
        }
    ).rules
    assert rule.priority == 10
    assert rule.action is None
    assert dict(rule.settings) == {
        "polling_interval": 30.0,
        "discovery_interval": 43200.0,
        "reachability_strategy": ReachabilityStrategy.SNMP_FALLBACK,
        "snmp_timeout": 5.0,
        "snmp_retries": 2,
        "adaptive_polling": True,
        "stale_after": 2592000.0,
    }
    primary = ("interest", InterestClass.PRIMARY)
    assert rule.annotations.items() == (primary, ("sticky", True), ("wan_circuit", True))
    physical, oper = rule.conditions[1:]
    assert [p.text for p in physical.patterns] == ["exact:true"]
    assert [p.text for p in oper.patterns] == ["exact:up", "exact:lower_layer_down"]


@pytest.mark.parametrize(
    ("table", "problem"),
    [
        ({"action": "include"}, "rules[0].name: required"),
        ({"name": "bad name", "action": "include"}, "rules[0].name: use 1-64 letters"),
        ({"name": "r", "action": "drop"}, "rules[0].action: expected one of: include, exclude"),
        ({"name": "r"}, "rules[0]: rule does nothing"),
        ({"name": "r", "action": "include", "colour": 1}, "rules[0].colour: unknown key"),
        ({"name": "r", "action": "include", "match": {"vlan": ["1"]}}, "match.vlan: unknown key"),
        ({"name": "r", "action": "include", "match": []}, "rules[0].match: expected a table"),
        ({"name": "r", "action": "include", "priority": 5000}, "priority: must be at most 1000"),
        ({"name": "r", "action": "include", "priority": "1"}, "priority: expected an integer"),
        ({"name": "r", "action": "include", "groups": "a"}, "groups: expected a list of patterns"),
        ({"name": "r", "action": "include", "match": {"physical": "yes"}}, "physical: expected"),
        ({"name": "r", "action": "include", "match": {"oper_status": []}}, "oper_status: expected"),
        ({"name": "r", "action": "include", "groups": ["a"], "sites": ["b"]}, "one scope"),
        ({"name": "r", "action": "include", "interfaces": ["Gi1"]}, "do not make one scope"),
        ({"name": "r", "action": "include", "scope": "device"}, 'scope "device" takes devices'),
        ({"name": "r", "action": "include", "scope": "global", "tags": ["a"]}, "no selector keys"),
        ({"name": "r", "action": "x", "exclude_ifname_patterns": ["a"]}, "use one shorthand key"),
    ],
)
def test_invalid_rules_name_the_path(table: dict[str, Any], problem: str) -> None:
    """Unknown keys, bad types, and inconsistent scopes are errors that name their path.

    Args:
        table: Invalid rule table.
        problem: Expected text of one problem.

    Returns:
        None

    Raises:
        None
    """
    found = _problems(table)
    assert any(problem in line for line in found), found
    assert all(line.startswith("interface_policy.rules[0]") for line in found)


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        ({"polling_interval": "10s"}, "polling_interval: must be between 30s and 1h"),
        ({"polling_interval": "2h"}, "must be between 30s and 1h"),
        ({"discovery_interval": "29s"}, "discovery_interval: must be between 30s and 30d"),
        ({"snmp_timeout": "500ms"}, "snmp_timeout: must be between 1s and 30s"),
        ({"snmp_retries": 6}, "snmp_retries: must be at most 5"),
        ({"stale_after": "1h"}, "stale_after: must be between 1d and 365d"),
        ({"polling_interval": "fast"}, "expected a duration"),
        ({"reachability_strategy": "ping"}, "expected one of: icmp_gated"),
        ({"adaptive_polling": "on"}, "expected true or false"),
        ({"polling": "60s"}, "settings.polling: unknown key"),
    ],
)
def test_settings_outside_documented_limits_fail(settings: dict[str, Any], problem: str) -> None:
    """POLICY-007 / section 11.4: intervals below the safe minimum and other bad values fail.

    Args:
        settings: Invalid settings table.
        problem: Expected text of the problem.

    Returns:
        None

    Raises:
        None
    """
    found = _problems({"name": "r", "settings": settings})
    assert any(problem in line for line in found), found


def test_annotation_alias_conflict_and_duplicates_are_reported() -> None:
    """``high_priority`` must agree with ``interest``; rule names must be unique.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    found = _problems(
        {"name": "a", "annotations": {"interest": "primary", "high_priority": False}},
        {"name": "b", "action": "include"},
        {"name": "b", "action": "exclude"},
    )
    assert 'rules[0].annotations.high_priority: contradicts interest = "primary"' in found[0]
    assert found[1] == "interface_policy.rules[2].name: duplicate rule name (also rule #2)"
    (low,) = rules({"name": "a", "annotations": {"high_priority": False}}).rules
    assert low.annotations.interest is InterestClass.SECONDARY


@pytest.mark.parametrize(
    ("document", "problem"),
    [
        ({"rules": []}, "rules: unknown key; expected one of: interface_policy"),
        ({"interface_policy": {"rule": []}}, "interface_policy.rule: unknown key"),
        ({"interface_policy": {"rules": {}}}, "interface_policy.rules: expected a list"),
        ({"interface_policy": {"rules": [{}] * (MAX_RULES + 1)}}, f"at most {MAX_RULES}"),
        ({"interface_policy": {"rules": ["x"]}}, "rules[0]: expected a table"),
    ],
)
def test_document_shape_is_validated(document: dict[str, Any], problem: str) -> None:
    """Only ``interface_policy.rules`` is accepted at the top of a policies file.

    Args:
        document: Invalid document.
        problem: Expected text of one problem.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(PolicyError) as caught:
        parse_policy(document)
    assert any(problem in str(issue) for issue in caught.value.issues)


def test_empty_document_is_an_empty_policy() -> None:
    """A policies file without rules is valid and changes nothing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert parse_policy({}, "policies.toml").rules == ()


@pytest.mark.parametrize(
    ("seconds", "text"), [(30.0, "30s"), (90.0, "90s"), (300.0, "5m"), (3600, "1h"), (0.5, "0.5s")]
)
def test_durations_render_compactly(seconds: float, text: str) -> None:
    """Explain output shows durations the way operators write them.

    Args:
        seconds: Duration in seconds.
        text: Expected rendering.

    Returns:
        None

    Raises:
        None
    """
    assert format_seconds(seconds) == text
    assert format_setting("polling_interval", seconds) == text
    assert format_setting("snmp_retries", 2) == "2"


def test_reader_rejects_wrong_types_and_long_lists() -> None:
    """Strings, string lists, and list lengths are checked like every other value.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    issues: list[PolicyIssue] = []
    table = {"name": 5, "tags": ["a", 1], "groups": ["g"] * 300}
    reader = TableReader(table, "t", issues, {"name", "tags", "groups"})
    assert reader.text("name") is None
    assert reader.strings("tags") == reader.strings("groups") == ()
    assert [str(i) for i in issues] == [
        "t.name: expected a string",
        "t.tags: expected a list of strings",
        "t.groups: at most 256 entries are allowed",
    ]
