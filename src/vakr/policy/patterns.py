r"""Policy patterns: shell glob by default, ``exact:``, and bounded ``re:`` (POLICY-005).

Operators select interfaces by name, description, and other facts. Three
explicit syntaxes keep intent unambiguous:

- ``Gi*`` (or ``glob:Gi*``) is a shell glob: ``*``, ``?``, ``[abc]``,
  ``[!abc]``. Case-insensitive, and it must match the whole value.
- ``exact:Vlan100`` matches exactly that value, case included.
- ``re:Te\d+/1/\d+`` is a regular expression that must match the whole
  value, case-insensitively. It is checked for catastrophic backtracking
  when the configuration loads (``regex_safety``).

Every pattern is limited to ``MAX_PATTERN_LENGTH`` characters, and values
longer than ``MAX_INPUT_LENGTH`` never match a glob or ``re:`` pattern,
which bounds the worst-case match time (CFG-005, SEC-019). SNMP values sit
well inside that: ``ifAlias`` is at most 64 characters (RFC 2863), and
``ifName``/``ifDescr`` at most 255.

Dependencies:
    fnmatch: Translates globs into linear-time regular expressions.
    vakr.policy.regex_safety: Static rejection of unsafe ``re:`` patterns.

Safety:
    Patterns are only ever compiled and matched, never passed to a shell
    (NETSEC-002). Control characters are rejected outright.

Example:
    >>> compile_pattern("Gi*", "p").matches("gigabitethernet0/0/0")
    True
    >>> compile_pattern("exact:Vlan100", "p").matches("vlan100")
    False
"""

import fnmatch
import re
from dataclasses import dataclass, field
from enum import StrEnum

from vakr.policy.errors import PolicyError, PolicyIssue
from vakr.policy.reader import MAX_LIST_LENGTH
from vakr.policy.regex_safety import MAX_INPUT_LENGTH, regex_problem

MAX_PATTERN_LENGTH = 256
"""Longest pattern accepted, prefix included."""

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PatternKind(StrEnum):
    """Which matching syntax a pattern uses."""

    GLOB = "glob"
    EXACT = "exact"
    REGEX = "re"


_PREFIXES = (("re:", PatternKind.REGEX), ("exact:", PatternKind.EXACT), ("glob:", PatternKind.GLOB))


@dataclass(frozen=True, slots=True)
class Pattern:
    """One compiled, validated pattern; immutable and safe to share.

    Attributes:
        text: The pattern as the operator wrote it, prefix included.
        kind: Glob, exact, or regular expression.
        body: The pattern without its prefix.
    """

    text: str
    kind: PatternKind
    body: str
    _regex: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def matches(self, value: str) -> bool:
        """Report whether ``value`` matches this pattern in full.

        Args:
            value: Fact value such as an ``ifName`` or a token.

        Returns:
            bool: ``True`` on a match. Glob and ``re:`` patterns never match
            values longer than ``MAX_INPUT_LENGTH``.

        Raises:
            None
        """
        if self._regex is None:
            return value == self.body
        return len(value) <= MAX_INPUT_LENGTH and self._regex.fullmatch(value) is not None


def _glob_problem(body: str) -> str | None:
    """Explain why a glob is invalid, or return ``None``.

    ``fnmatch`` silently treats an unterminated ``[`` as a literal, which is
    almost always a typo, so it is rejected here instead.

    Args:
        body: Glob without a prefix.

    Returns:
        str | None: Reason, or ``None`` when valid.

    Raises:
        None
    """
    if not body:
        return 'empty glob; write "exact:" to match an empty value'
    index = 0
    while index < len(body):
        if body[index] == "[":
            end = index + 1
            end += body[end : end + 1] == "!"
            end += body[end : end + 1] == "]"
            end = body.find("]", end)
            if end < 0:
                return f'unterminated "[" at position {index} in glob'
            index = end
        index += 1
    return None


def _split(text: str) -> tuple[PatternKind, str]:
    """Separate the syntax prefix from the pattern body.

    Args:
        text: Pattern as written.

    Returns:
        tuple[PatternKind, str]: The kind and the body.

    Raises:
        None
    """
    for prefix, kind in _PREFIXES:
        if text.startswith(prefix):
            return kind, text[len(prefix) :]
    return PatternKind.GLOB, text


def _problem(text: str, kind: PatternKind, body: str) -> str | None:
    """Explain why a pattern is invalid or unsafe, or return ``None``.

    Args:
        text: Pattern as written.
        kind: Its syntax.
        body: Text after the prefix.

    Returns:
        str | None: Reason, or ``None`` when valid.

    Raises:
        None
    """
    if len(text) > MAX_PATTERN_LENGTH:
        return f"pattern is {len(text)} characters; the limit is {MAX_PATTERN_LENGTH}"
    if _CONTROL.search(text):
        return "control characters are not allowed in patterns"
    if kind is PatternKind.GLOB:
        return _glob_problem(body)
    if kind is PatternKind.REGEX:
        return "empty regular expression" if not body else regex_problem(body)
    return None


def compile_pattern(text: object, path: str) -> Pattern:
    """Validate and compile one pattern.

    Args:
        text: Pattern as loaded from TOML or JSON; must be a string.
        path: Dotted location for error messages.

    Returns:
        Pattern: The compiled pattern.

    Raises:
        PolicyError: If the pattern is not a string, too long, contains
            control characters, is an invalid glob, or is an invalid or
            unsafe regular expression.
    """
    if not isinstance(text, str):
        raise PolicyError([PolicyIssue(path, "expected a pattern string")])
    kind, body = _split(text)
    reason = _problem(text, kind, body)
    if reason:
        raise PolicyError([PolicyIssue(path, reason)])
    if kind is PatternKind.EXACT:
        return Pattern(text, kind, body)
    source = fnmatch.translate(body) if kind is PatternKind.GLOB else body
    return Pattern(text, kind, body, re.compile(source, re.IGNORECASE))


def compile_patterns(value: object, path: str, issues: list[PolicyIssue]) -> tuple[Pattern, ...]:
    """Compile a non-empty list of patterns, collecting problems.

    Args:
        value: List of pattern strings as loaded.
        path: Dotted location of the list.
        issues: List that problems are appended to.

    Returns:
        tuple[Pattern, ...]: The valid patterns, in input order.

    Raises:
        None
    """
    if not isinstance(value, list):
        issues.append(PolicyIssue(path, "expected a list of patterns"))
        return ()
    if not value or len(value) > MAX_LIST_LENGTH:
        issues.append(PolicyIssue(path, f"expected 1 to {MAX_LIST_LENGTH} patterns"))
        return ()
    patterns: list[Pattern] = []
    for index, text in enumerate(value):
        try:
            patterns.append(compile_pattern(text, f"{path}[{index}]"))
        except PolicyError as exc:
            issues.extend(exc.issues)
    return tuple(patterns)
