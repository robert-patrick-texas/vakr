"""POLICY-002/004/006, META-012, DISC-007: precedence, tie-breaking, and explain traces.

Dependencies:
    pytest: Test runner.
    vakr.policy.engine: The engine under test.

Example:
    $ uv run pytest tests/unit/policy/test_engine.py
"""

from tests.unit.policy.policy_fixtures import defaults, edge, rules
from vakr.domain.enums import IfOperStatus, InterestClass
from vakr.policy.engine import decide, explain
from vakr.policy.facts import Annotations, Circuit, InterfaceFacts, MatchField, field_values
from vakr.policy.keywords import KeywordSet
from vakr.policy.resolve import resolve
from vakr.policy.rules import Policy
from vakr.policy.trace import Source

NO_KEYWORDS = KeywordSet()


def test_tier_outranks_priority_and_file_order() -> None:
    """POLICY-002: interface override > device > group/tag > global, whatever the priority.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules(
        {"name": "dev-include", "devices": ["edge-rtr1"], "action": "include"},
        {"name": "grp-exclude", "groups": ["wan-routers"], "action": "exclude", "priority": 1000},
        {"name": "all-exclude", "action": "exclude", "priority": 1000},
    )
    result = explain(edge("Vlan100"), policy, NO_KEYWORDS)
    assert result.decision.monitored
    assert result.decision.effective_rule == "dev-include"
    assert [t.rule.name for t in result.rules] == ["all-exclude", "grp-exclude", "dev-include"]
    (monitor,) = [v for v in result.values if v.name == "monitor"]
    assert [c.origin for c in monitor.overridden] == ["grp-exclude", "all-exclude", "default"]
    override = rules(
        {
            "name": "if-exclude",
            "devices": ["edge-rtr1"],
            "interfaces": ["Vlan*"],
            "action": "exclude",
        },
        {"name": "dev-include", "devices": ["edge-rtr1"], "action": "include", "priority": 1000},
    )
    assert decide(edge("Vlan100"), override, NO_KEYWORDS).effective_rule == "if-exclude"


def test_priority_then_later_rule_breaks_ties() -> None:
    """POLICY-002: within a tier the higher priority wins, then the later rule in the file.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    same_priority = rules(
        {"name": "no-svis", "exclude_ifname_patterns": ["Vlan*"]},
        {"name": "mgmt-svi", "include_ifname_patterns": ["exact:Vlan100"]},
    )
    assert decide(edge("Vlan100"), same_priority, NO_KEYWORDS).effective_rule == "mgmt-svi"
    prioritised = rules(
        {"name": "no-svis", "exclude_ifname_patterns": ["Vlan*"], "priority": 1},
        {"name": "mgmt-svi", "include_ifname_patterns": ["exact:Vlan100"]},
    )
    decision = decide(edge("Vlan100"), prioritised, NO_KEYWORDS)
    assert (decision.monitored, decision.effective_rule) == (False, "no-svis")


def test_suppressed_interfaces_stay_inventoried() -> None:
    """DISC-007: an excluded interface is reported as not monitored, never as absent.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules({"name": "no-loopbacks", "exclude_ifname_patterns": ["Loopback*"]})
    decision = decide(edge("Loopback0"), policy, NO_KEYWORDS)
    assert (decision.monitored, decision.inventoried) == (False, True)


def test_settings_merge_field_by_field_across_tiers() -> None:
    """A device rule's timeout and a tag rule's interval combine; each keeps its winner.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules(
        {"name": "global-slow", "settings": {"polling_interval": "300s", "snmp_retries": 1}},
        {"name": "wan-fast", "tags": ["wan"], "settings": {"polling_interval": "30s"}},
        {"name": "sat-link", "devices": ["edge-rtr1"], "settings": {"snmp_timeout": "10s"}},
    )
    result = explain(edge("GigabitEthernet0/0/1"), policy, defaults())
    assert dict(result.decision.settings) == {
        "polling_interval": 30.0,
        "snmp_timeout": 10.0,
        "snmp_retries": 1,
    }
    (slow, fast, sat) = result.rules
    assert (slow.won, fast.won, sat.won) == (
        ("snmp_retries",),
        ("polling_interval",),
        ("snmp_timeout",),
    )
    assert slow.offers == ("polling_interval", "snmp_retries")


def test_meta_012_annotation_then_keyword_then_rule_then_default() -> None:
    """META-012: explicit annotation > description keyword > policy rule > default.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    demote = rules({"name": "demote", "annotations": {"interest": "secondary", "sticky": False}})
    # Keyword beats the rule.
    keyword = explain(edge("GigabitEthernet0/0/0"), demote, defaults())
    interest = next(v for v in keyword.values if v.name == "interest")
    assert (interest.winner.source, interest.winner.origin) == (Source.KEYWORD, "ISP")
    assert [c.origin for c in interest.overridden] == ["demote", "default"]
    # The explicit annotation beats both.
    pinned = edge("GigabitEthernet0/0/0", annotations=Annotations(interest=InterestClass.SECONDARY))
    assert decide(pinned, demote, defaults()).interest is InterestClass.SECONDARY
    # A rule applies when no keyword matches, and the default when nothing does.
    promote = rules({"name": "promote", "annotations": {"interest": "primary", "sticky": True}})
    assert decide(edge("Vlan100"), promote, defaults()).interest is InterestClass.PRIMARY
    assert decide(edge("Vlan100"), Policy(), defaults()).interest is InterestClass.SECONDARY
    sticky = next(
        v for v in explain(edge("Vlan100"), promote, defaults()).values if v.name == "sticky"
    )
    assert (sticky.winner.source, sticky.winner.value) == (Source.RULE, True)


def test_keywords_imply_wan_circuit_and_tags_feed_rule_matching() -> None:
    """ISP/WAN imply a WAN circuit, UPLINK does not; description tags select tag rules.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules({"name": "wan-sticky", "tags": ["wan", "isp"], "annotations": {"sticky": True}})
    isp = decide(edge("GigabitEthernet0/0/0"), policy, defaults())
    uplink = decide(edge("TenGigabitEthernet1/1/1"), policy, defaults())
    assert (isp.wan_circuit, isp.sticky, isp.description_tags) == (True, True, ("isp",))
    assert (uplink.wan_circuit, uplink.sticky, uplink.interest) == (
        False,
        False,
        InterestClass.PRIMARY,
    )
    assert uplink.description_tags == ("uplink",)


def test_conditions_and_together_and_or_within_a_list() -> None:
    """A rule matches only when every condition matches; any pattern in a list will do.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules(
        {
            "name": "down-tunnels",
            "action": "exclude",
            "match": {"ifname": ["Loopback*", "Tunnel*"], "oper_status": ["lower_layer_down"]},
        }
    )
    down = edge("Tunnel10", oper_status=IfOperStatus.LOWER_LAYER_DOWN)
    assert not decide(down, policy, NO_KEYWORDS).monitored
    up = explain(edge("Tunnel10"), policy, NO_KEYWORDS)
    (trace,) = up.rules
    assert not trace.matched
    assert trace.deciding is not None
    assert (trace.deciding.field.value, trace.deciding.values) == ("oper_status", ("up",))
    assert [check.matched for check in trace.checks] == [True, False]


def test_unknown_facts_match_nothing_and_say_so() -> None:
    """Missing data is never coerced: an unknown site matches no site pattern.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules({"name": "nyc", "sites": ["*"], "action": "exclude"})
    (trace,) = explain(edge("Vlan100", site=None), policy, NO_KEYWORDS).rules
    assert trace.deciding is not None
    assert trace.deciding.note == "value unknown"
    (tags,) = explain(
        edge("Vlan100"), rules({"name": "t", "tags": ["x"], "action": "include"}), NO_KEYWORDS
    ).rules
    assert tags.deciding is not None
    assert tags.deciding.note == ""


def test_long_values_are_contained_in_the_trace() -> None:
    """CFG-005: an over-long description never reaches a pattern, and the trace says why.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    policy = rules({"name": "any-desc", "match": {"ifalias": ["*"]}, "action": "exclude"})
    result = explain(edge("Vlan100", alias="WAN " * 200), policy, defaults())
    (trace,) = result.rules
    assert trace.deciding is not None
    assert "never match" in trace.deciding.note
    assert result.classification.too_long
    assert result.decision.description_tags == ()


def test_evaluation_is_deterministic() -> None:
    """POLICY-006: the same input gives the same decision and trace, every time.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tables = [
        {"name": "b", "tags": ["wan"], "settings": {"polling_interval": "30s"}},
        {"name": "a", "exclude_ifname_patterns": ["*"]},
        {"name": "c", "groups": ["wan-routers"], "include_ifalias_patterns": ["*WAN*"]},
    ]
    facts = edge("GigabitEthernet0/0/1")
    first = explain(facts, rules(*tables), defaults())
    assert all(explain(facts, rules(*tables), defaults()) == first for _ in range(5))
    assert first.decision.effective_rule == "c"


def test_every_match_field_reads_its_fact() -> None:
    """POLICY-003: each match field reads the right fact, in a stable order.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    facts = edge(
        "GigabitEthernet0/0/0",
        device_tags=("edge",),
        addresses=("198.51.100.2/30",),
        physical=None,
        annotations=Annotations(tags=("carrier-x",)),
        circuit=Circuit(provider="Carrier-X", circuit_id="ABC12345"),
    )
    expected = {
        MatchField.DEVICE: ("edge-rtr1",),
        MatchField.DEVICE_ADDRESS: ("192.0.2.1",),
        MatchField.SITE: ("nyc01",),
        MatchField.DEVICE_TAGS: ("edge",),
        MatchField.DEVICE_GROUPS: ("wan-routers",),
        MatchField.TAGS: ("edge", "carrier-x", "isp"),
        MatchField.INTERFACE_TAGS: ("carrier-x", "isp"),
        MatchField.IFNAME: ("GigabitEthernet0/0/0",),
        MatchField.IFDESCR: ("GigabitEthernet0/0/0",),
        MatchField.IFALIAS: ("ISP: Carrier-X 1G ckt ABC12345",),
        MatchField.IFTYPE: ("6",),
        MatchField.IFINDEX: ("1",),
        MatchField.ADDRESSES: ("198.51.100.2/30",),
        MatchField.PHYSICAL: (),
        MatchField.ADMIN_STATUS: ("up",),
        MatchField.OPER_STATUS: ("up",),
        MatchField.PROVIDER: ("Carrier-X",),
        MatchField.CIRCUIT_ID: ("ABC12345",),
    }
    assert {f: field_values(facts, f, ("isp",)) for f in MatchField} == expected
    assert field_values(edge("Vlan100"), MatchField.PHYSICAL) == ("false",)
    unnamed = InterfaceFacts("edge-rtr1", if_index=20)
    assert unnamed.label == "edge-rtr1 ifIndex 20"
    assert field_values(unnamed, MatchField.IFNAME) == ()


def test_settings_without_offers_have_no_value() -> None:
    """A setting no rule changes keeps the configured default and is not traced.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert resolve("polling_interval", [], []) is None
    assert explain(edge("Vlan100"), Policy(), NO_KEYWORDS).decision.settings == ()
