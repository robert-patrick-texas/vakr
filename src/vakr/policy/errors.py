"""Validation errors for policy rules, patterns, keywords, and fact files.

A policy mistake must stop at load time with a message an operator can act
on: the dotted path of the offending value and the reason, for example
``interface_policy.rules[2].match.ifname[0]: unterminated "[" in glob``
(POLICY-005, section 11.4). Parsers collect every problem before raising,
so one ``vakr policy validate`` run lists them all.

Dependencies:
    dataclasses: Immutable issue records.

Safety:
    Messages name paths and describe the problem; they repeat at most the
    offending pattern text, never credentials (policies hold none).

Example:
    >>> str(PolicyIssue("rules[0].priority", "expected an integer"))
    'rules[0].priority: expected an integer'
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyIssue:
    """One validation problem at one location.

    Attributes:
        path: Dotted path with list indexes, such as ``rules[1].action``.
        reason: Operator-readable explanation of what is wrong.
    """

    path: str
    reason: str

    def __str__(self) -> str:
        """Format the issue as ``path: reason``.

        Args:
            None

        Returns:
            str: The issue on one line.

        Raises:
            None
        """
        return f"{self.path}: {self.reason}"


class PolicyError(ValueError):
    """Policy, keyword, or facts input is invalid and must not be used.

    Attributes:
        issues: Every problem found, in input order.
    """

    def __init__(self, issues: Sequence[PolicyIssue]) -> None:
        """Record the problems and build a multi-line message.

        Args:
            issues: Problems found; at least one.

        Returns:
            None

        Raises:
            None
        """
        self.issues = tuple(issues)
        lines = "\n".join(str(issue) for issue in self.issues)
        super().__init__(f"invalid policy input ({len(self.issues)} problem(s)):\n{lines}")


def raise_if_any(issues: Sequence[PolicyIssue]) -> None:
    """Raise ``PolicyError`` when a parser collected any issue.

    Args:
        issues: Problems collected so far.

    Returns:
        None

    Raises:
        PolicyError: If ``issues`` is not empty.
    """
    if issues:
        raise PolicyError(issues)
