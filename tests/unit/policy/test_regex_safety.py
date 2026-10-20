"""CFG-005 / SEC-019: catastrophic-backtracking regexes are rejected; safe ones stay fast.

The adversarial inputs are the classic near-misses that make a vulnerable
pattern backtrack, at the 512-character input cap. Every accepted pattern
must finish them well inside the budget; every rejected shape names why.

Dependencies:
    pytest: Parametrised cases.
    vakr.policy.regex_safety: The analysis under test.

Example:
    $ uv run pytest tests/unit/policy/test_regex_safety.py
"""

import re
import time
from types import SimpleNamespace

import pytest

from vakr.policy import regex_safety
from vakr.policy.patterns import MAX_INPUT_LENGTH
from vakr.policy.regex_safety import BRANCH, EMPTY_LOOP, NESTED, regex_problem

SAFE = [
    r"(Gi|Te)\d+/\d+/\d+",
    r"TenGigabitEthernet\d+/\d+/\d+(\.\d+)?",
    r"(\d{1,3}\.){3}\d{1,3}",
    r"(\d{1,3}\.)+",
    r".*WAN.*",
    r".*ISP.*WAN.*",
    r"[^/]+/[^/]+",
    r"(ab+c)+",
    r"CKT[0-9]+",
    r"(?>a|ab)+",
    r"(a+)++b",
    r"Vlan(1\d\d|2[0-4]\d)",
    r"Port-channel\d+",
    r"(?i)loopback\d+",
    r"\bWAN\b.*",
    r"(?=Gi)\w+\d",
    r"[A-Z]{3}\d{5}",
    r"[^\W\d]+\d",
    r"é+x",
    r"(ab|a)*c",  # CPython factors this into a(?:b|), which is linear
    r"(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)(a|ab)",
    "(?:a|\\w)" * 11,  # single characters merge into one class
    # Word, space, and digit classes are disjoint, Unicode included.
    r"(\w+\s)+",
    r"(\S+\s)+WAN",
    r"(\D+\d)+",
    r"([^\s]+\s)+",
    r"(\w++\s?)*",  # the possessive rewrite of a rejected pattern
    r"(?:ab|\wb){2}.*a.*c",  # 4 choices x two repeats stays inside the step budget
    r"(?:ab|\wb){10}.*c",
]

UNSAFE = [
    (r"(a+)+b", NESTED),
    (r"(\w+\s?)*$", NESTED),
    (r"(x+x+)+y", NESTED),
    (r"(\d{1,3}\.?)+", NESTED),
    (r"(a{1,3})+b", NESTED),
    (r"([a-z]+)*\.", NESTED),
    (r"(a?a)+", NESTED),
    # Case-insensitive matching pairs k with the Kelvin sign and s with long s.
    (f"(k+{chr(0x212A)})+", NESTED),
    (f"(s+{chr(0x017F)})+", NESTED),
    ("(é+É)+", NESTED),
    (r"(a|aa)+b", BRANCH),
    (r"(a|a)+", BRANCH),
    (r"(?:ab|\wc)+", BRANCH),
    (r"(a*)*", EMPTY_LOOP),
    (r"(\b)+", EMPTY_LOOP),
    (r".*a.*a.*b", "more than 2 open-ended repeats"),
    (r"(.*a){3}", "more than 2 open-ended repeats"),
    ("a?" * 12 + "a" * 12, "more than 1024 combinations"),
    (r"(?:ab|\wc)" * 11, "more than 1024"),
    # Choices multiply with overlapping repeats: each of these measured over 1 s.
    (r"(?:ab|\wb){10}.*a.*c", "overlapping optional parts around open-ended repeats"),
    ("a?" * 10 + ".*a.*c", "overlapping optional parts around open-ended repeats"),
    (r"(a)\1", "backreferences"),
    (r"(?P<x>a)(?(x)b|c)", "backreferences"),
]

ADVERSARIAL = (
    "a" * MAX_INPUT_LENGTH,
    "1" * (MAX_INPUT_LENGTH - 1) + "x",
    "1." * (MAX_INPUT_LENGTH // 2 - 1) + "x",
    "ISPWAN" * (MAX_INPUT_LENGTH // 6) + "!",
    "/" * MAX_INPUT_LENGTH,
    "Gi" * (MAX_INPUT_LENGTH // 2),
)


@pytest.mark.parametrize("pattern", SAFE)
def test_realistic_patterns_are_accepted_and_fast(pattern: str) -> None:
    """Interface-name and description patterns pass and stay fast on near-miss input.

    Args:
        pattern: Accepted regular expression.

    Returns:
        None

    Raises:
        None
    """
    assert regex_problem(pattern) is None
    compiled = re.compile(pattern, re.IGNORECASE)
    for text in ADVERSARIAL:
        start = time.perf_counter()
        compiled.fullmatch(text)
        assert time.perf_counter() - start < 0.05, (pattern, text[:20])


@pytest.mark.parametrize(("pattern", "reason"), UNSAFE)
def test_catastrophic_shapes_are_rejected(pattern: str, reason: str) -> None:
    """CFG-005: nested quantifiers, overlapping alternatives, and the rest are refused.

    Args:
        pattern: Unsafe regular expression.
        reason: Expected start of the reason.

    Returns:
        None

    Raises:
        None
    """
    problem = regex_problem(pattern)
    assert problem is not None
    assert problem.startswith(reason)


def test_rejected_shape_really_is_slow() -> None:
    """The analysis is not hypothetical: ``(a+)+b`` doubles its time per character.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    compiled = re.compile(r"(a+)+b")
    timings = []
    for length in (14, 18):
        start = time.perf_counter()
        compiled.fullmatch("a" * length)
        timings.append(time.perf_counter() - start)
    assert timings[1] > timings[0] * 4


def test_invalid_and_deep_patterns_report_a_reason() -> None:
    """Syntax errors and absurd nesting are reported, never raised.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert (regex_problem("(a") or "").startswith("invalid regular expression")
    assert regex_problem("(" * 1000 + "a" + ")" * 1000) == "regular expression is nested too deeply"


def test_unknown_parser_nodes_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parse node the analysis does not know is rejected, never trusted.

    Args:
        monkeypatch: Replaces the parser with one returning an unknown node.

    Returns:
        None

    Raises:
        None
    """
    tree = [(SimpleNamespace(name="FUTURE_OPCODE"), None)]
    monkeypatch.setattr(regex_safety, "_SRE", SimpleNamespace(parse=lambda _body, _flags: tree))
    assert regex_problem("x") == "unsupported construct FUTURE_OPCODE"
