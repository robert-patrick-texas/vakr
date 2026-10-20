"""Collection policy: which interfaces are monitored, how, and why (POLICY-001..007).

NetOps labels the interfaces it cares about in their descriptions (ISP,
WAN, Uplink), suppresses noise such as loopbacks and SVIs on access
switches, and polls a few circuits more closely than the rest. This package
turns ordered policy rules and description keywords into one deterministic
decision per interface, together with a trace that names every rule
considered, so an operator can always answer "why is this interface (not)
graphed?" (POLICY-004).

Modules:
    patterns: Glob, ``exact:``, and bounded ``re:`` patterns (POLICY-005).
    regex_safety: Static rejection of catastrophic-backtracking regexes.
    facts: What a rule can match on, independent of the domain model.
    rules, settings: Parsing ``policies.toml`` rules and their actions.
    classify: Description keyword tags (META-012, META-013).
    engine, trace, render: Precedence, explain traces, and their output.
    ordering: Sticky/primary-first interface display order (LIFE-007).

Dependencies:
    vakr.domain.enums: Shared interest, reachability, and IF-MIB states.

Safety:
    Every function here except ``files`` is pure: no I/O, no clock, no
    globals, so the same facts and rules always give the same answer
    (POLICY-006). Suppression never removes an interface from inventory
    (DISC-007).

Example:
    >>> from vakr.policy.patterns import compile_pattern
    >>> compile_pattern("Gi*", "match.ifname[0]").matches("GigabitEthernet0/0/0")
    True
"""
