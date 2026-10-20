"""CFG-005 / POLICY-005: glob, ``exact:``, and ``re:`` pattern syntax and validation.

Dependencies:
    pytest: Parametrised cases.
    vakr.policy.patterns: The module under test.

Example:
    $ uv run pytest tests/unit/policy/test_patterns.py
"""

import pytest

from vakr.policy.errors import PolicyError, PolicyIssue
from vakr.policy.patterns import (
    MAX_INPUT_LENGTH,
    MAX_PATTERN_LENGTH,
    PatternKind,
    compile_pattern,
    compile_patterns,
)


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("Gi*", "GigabitEthernet0/0/0", True),
        ("gi*", "GigabitEthernet0/0/0", True),  # globs ignore case
        ("Gi*", "TenGigabitEthernet1/1/1", False),  # anchored at the start
        ("*0/0/0", "GigabitEthernet0/0/0", True),
        ("Vlan1??", "Vlan100", True),
        ("Vlan1??", "Vlan10", False),  # anchored at the end
        ("Port-channel[0-9]", "Port-channel1", True),
        ("Loopback[!0]", "Loopback0", False),
        ("glob:Tunnel*", "Tunnel10", True),
        ("glob:re:x", "re:x", True),  # an explicit glob prefix makes "re:" literal
        ("exact:Vlan100", "Vlan100", True),
        ("exact:Vlan100", "vlan100", False),  # exact means exact, case included
        ("exact:", "", True),
        ("exact:Vlan1*", "Vlan100", False),
        (r"re:Te\d+/1/\d+", "TenGigabitEthernet1/1/1", False),  # full match, not search
        (r"re:Te\w*\d+/1/\d+", "TenGigabitEthernet1/1/1", True),
        (r"re:.*ISP.*", "ISP: Carrier-X 1G ckt ABC12345", True),
        (r"re:.*isp.*", "ISP: Carrier-X 1G ckt ABC12345", True),  # re: ignores case
    ],
)
def test_pattern_syntax_and_case_rules(pattern: str, value: str, expected: bool) -> None:
    """Globs and ``re:`` are case-insensitive and anchored; ``exact:`` is byte-exact.

    Args:
        pattern: Pattern as an operator writes it.
        value: Fact value such as an ``ifName``.
        expected: Whether it should match.

    Returns:
        None

    Raises:
        None
    """
    assert compile_pattern(pattern, "p").matches(value) is expected


def test_kind_and_body_are_recorded() -> None:
    """The compiled pattern remembers its syntax and the text after the prefix.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert compile_pattern("exact:Vlan100", "p").kind is PatternKind.EXACT
    regex = compile_pattern(r"re:CKT\d+", "p")
    assert (regex.kind, regex.body, regex.text) == (PatternKind.REGEX, r"CKT\d+", r"re:CKT\d+")
    assert compile_pattern("Gi*", "p").kind is PatternKind.GLOB


@pytest.mark.parametrize(
    ("pattern", "reason"),
    [
        ("Gi[0-9", 'unterminated "[" at position 2 in glob'),
        ("Gi[!", 'unterminated "["'),
        ("", "empty glob"),
        ("glob:", "empty glob"),
        ("re:", "empty regular expression"),
        ("re:(Gi", "invalid regular expression: missing ), unterminated subpattern"),
        ("re:[z-a]", "invalid regular expression: bad character range"),
        ("Gi\x00*", "control characters are not allowed"),
        ("exact:ISP\n", "control characters are not allowed"),
        ("G" * (MAX_PATTERN_LENGTH + 1), f"the limit is {MAX_PATTERN_LENGTH}"),
        ("re:(a+)+b", "nested quantifier"),
    ],
)
def test_invalid_patterns_fail_with_path_and_reason(pattern: str, reason: str) -> None:
    """CFG-005: invalid glob/regex, oversized and unsafe patterns are rejected with a reason.

    Args:
        pattern: Invalid pattern.
        reason: Expected start of the reason.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(PolicyError) as caught:
        compile_pattern(pattern, "interface_policy.rules[0].match.ifname[0]")
    (issue,) = caught.value.issues
    assert issue.path == "interface_policy.rules[0].match.ifname[0]"
    assert reason in issue.reason


def test_bracket_edge_cases_follow_fnmatch() -> None:
    """``[]]`` and ``[!]]`` are complete classes, as in ``fnmatch``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert compile_pattern("x[]]", "p").matches("x]")
    assert compile_pattern("x[!]]", "p").matches("xa")


def test_non_string_pattern_is_rejected() -> None:
    """A number where a pattern belongs is a validation error, not a crash.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(PolicyError, match="expected a pattern string"):
        compile_pattern(100, "match.ifname[0]")


def test_match_input_is_capped() -> None:
    """CFG-005: values over the input cap never reach a glob or regex; ``exact:`` still works.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    long_value = "x" * (MAX_INPUT_LENGTH + 1)
    assert not compile_pattern("*", "p").matches(long_value)
    assert not compile_pattern("re:x*", "p").matches(long_value)
    assert compile_pattern(f"exact:{long_value[:200]}", "p").matches(long_value[:200])
    assert compile_pattern("*", "p").matches("x" * MAX_INPUT_LENGTH)


def test_compile_patterns_collects_every_problem() -> None:
    """A list reports each bad entry by index and keeps the good ones.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    issues: list[PolicyIssue] = []
    patterns = compile_patterns(["Vlan*", "Gi[0", "re:(a*)*"], "m.ifname", issues)
    assert [p.text for p in patterns] == ["Vlan*"]
    assert [i.path for i in issues] == ["m.ifname[1]", "m.ifname[2]"]


@pytest.mark.parametrize(
    ("value", "reason"),
    [("Vlan*", "expected a list of patterns"), ([], "expected 1 to 256 patterns")],
)
def test_compile_patterns_requires_a_non_empty_list(value: object, reason: str) -> None:
    """An empty list would silently match nothing, so it is an error.

    Args:
        value: Invalid list value.
        reason: Expected reason.

    Returns:
        None

    Raises:
        None
    """
    issues: list[PolicyIssue] = []
    assert compile_patterns(value, "m.ifname", issues) == ()
    assert [str(i) for i in issues] == [f"m.ifname: {reason}"]
