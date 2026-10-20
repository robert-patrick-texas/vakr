r"""Reject regular expressions whose matching time could explode (POLICY-005, CFG-005).

Python's ``re`` has no match timeout and holds the GIL while it runs, so
one pattern such as ``(a+)+b`` against a description that almost matches
can freeze the poller. vakr refuses these patterns when configuration loads:

- nested quantifiers whose inner part can overlap what follows it, such as
  ``(a+)+`` or ``(\w+\s?)*``, and repeated overlapping alternatives such as
  ``(a|aa)+`` (both exponential), and repeats of nothing, such as ``(a*)*``;
- more than ``MAX_OVERLAPPING_LOOPS`` overlapping open-ended repeats
  (``.*a.*b.*c``), more than ``MAX_CHOICES`` overlapping optional parts, or
  both together beyond ``MAX_STEPS`` estimated steps (polynomial);
- backreferences and conditional groups (NP-hard in general).

An accepted pattern costs at most about ``MAX_STEPS`` steps on a
``MAX_INPUT_LENGTH`` value: under 10 ms measured on Python 3.15.

A part "overlaps" when the characters that can start it can also start what
comes after it, so the engine has a real choice to backtrack over: ``\d+/``
has none (a digit is never ``/``), ``.*a`` does. Possessive quantifiers
(``*+``, ``++``) and atomic groups (``(?>...)``) never give back what they
matched, so only their insides are checked.

Dependencies:
    importlib: Loads CPython's own parser, ``re._parser``, so the analysis
        judges exactly the tree ``re`` executes.
    vakr.policy.charsets: First-character sets of parse-tree items.

Safety:
    ``re._parser`` is private. Unknown nodes are rejected, so a CPython
    change can only make validation stricter; tests pin the known shapes.

Example:
    >>> regex_problem(r"(Gi|Te)\d+/\d+/\d+") is None
    True
    >>> regex_problem(r"(a+)+b").startswith("nested quantifier")
    True
"""

import importlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from vakr.policy.charsets import NONE, REPEATS, UnsafeRegexError, first, first_item

_SRE: Any = importlib.import_module("re._parser")

MAX_INPUT_LENGTH = 512
"""Longest value a glob or ``re:`` pattern is ever matched against."""

MAX_OVERLAPPING_LOOPS = 2
"""Open-ended overlapping repeats allowed; match cost grows as n to this power."""

MAX_CHOICES = 1024
"""Combinations of overlapping optional parts and alternatives allowed."""

MAX_STEPS = 2**21
"""Estimated steps allowed: choices times ``MAX_INPUT_LENGTH`` to the power of loops."""

SMALL_REPEAT = 16
"""Repeats bounded at or below this count cost a constant factor, not a power of n."""

NESTED = (
    "nested quantifier: a repeated part that can overlap what follows it sits inside "
    "another repeat, which can take exponential time; simplify it or make the inner "
    "repeat possessive (*+, ++)"
)
BRANCH = (
    "alternatives that can start with the same character are repeated, which can take "
    "exponential time; make them start differently or use an atomic group (?>...)"
)
EMPTY_LOOP = "a quantifier repeats a part that can match nothing, such as (a*)*"
_COMMITTED = frozenset({"ATOMIC_GROUP", "POSSESSIVE_REPEAT", "ASSERT", "ASSERT_NOT"})


@dataclass(frozen=True, slots=True)
class _Context:
    """Where the walk is: inside a backtracking repeat, and how many copies."""

    in_loop: bool = False
    copies: int = 1


@dataclass(slots=True)
class _Budget:
    """Running cost estimate for one pattern."""

    loops: int = 0
    choices: int = 1

    def check(self) -> None:
        """Raise if the estimate exceeds any budget.

        The loop count is checked first, so the combined estimate is never
        computed with a huge exponent.

        Args:
            None

        Returns:
            None

        Raises:
            UnsafeRegexError: If loops, choices, or their product is over budget.
        """
        if self.loops > MAX_OVERLAPPING_LOOPS:
            raise UnsafeRegexError(
                f"more than {MAX_OVERLAPPING_LOOPS} open-ended repeats overlap their "
                "neighbours (such as .*a.*b.*c), which can take polynomial time"
            )
        if self.choices > MAX_CHOICES:
            raise UnsafeRegexError(
                f"more than {MAX_CHOICES} combinations of overlapping optional parts"
            )
        if self.choices * MAX_INPUT_LENGTH**self.loops > MAX_STEPS:
            raise UnsafeRegexError(
                "overlapping optional parts around open-ended repeats (such as a?a?a?.*a.*b) "
                "could take too long; remove the optional parts or one of the repeats"
            )

    def add_loop(self, copies: int) -> None:
        """Count an open-ended overlapping repeat.

        Args:
            copies: How many times fixed ``{k}`` repeats duplicate it.

        Returns:
            None

        Raises:
            UnsafeRegexError: If the pattern is now over budget.
        """
        self.loops += copies
        self.check()

    def add_choices(self, options: int, copies: int) -> None:
        """Count a choice point with ``options`` ways to match.

        Args:
            options: Alternatives or repeat counts the engine may try.
            copies: How many times fixed ``{k}`` repeats duplicate it.

        Returns:
            None

        Raises:
            UnsafeRegexError: If the pattern is now over budget.
        """
        for _ in range(min(copies, MAX_CHOICES)):
            self.choices *= options
            self.check()


def _walk(seq: Sequence[Any], follow: int, ctx: _Context, budget: _Budget) -> None:
    """Check every item of ``seq``, given what can follow the sequence.

    Args:
        seq: Parser items in order.
        follow: Characters that can come right after ``seq``.
        ctx: Enclosing repeat context.
        budget: Running cost estimate.

    Returns:
        None

    Raises:
        UnsafeRegexError: If any item is unsafe.
    """
    after = follow
    for op, av in reversed(list(seq)):
        _walk_item(op.name, av, after, ctx, budget)
        item, nullable = first_item(op.name, av)
        after = item | (after if nullable else NONE)


def _walk_item(name: str, av: Any, follow: int, ctx: _Context, budget: _Budget) -> None:
    """Check one parser item and its children.

    Args:
        name: Parser opcode name.
        av: The opcode's argument.
        follow: Characters that can come right after this item.
        ctx: Enclosing repeat context.
        budget: Running cost estimate.

    Returns:
        None

    Raises:
        UnsafeRegexError: If the item or a child is unsafe.
    """
    if name in REPEATS:
        _walk_repeat(av, follow, ctx, budget)
    elif name == "BRANCH":
        _walk_branch(av[1], follow, ctx, budget)
    elif name == "SUBPATTERN":
        _walk(av[3], follow, ctx, budget)
    elif name in _COMMITTED:
        # Committed or zero-width parts are never re-entered by backtracking.
        body = av if name == "ATOMIC_GROUP" else av[-1]
        _walk(body, NONE, replace(ctx, in_loop=False), budget)
    else:
        first_item(name, av)


def _walk_repeat(av: Any, follow: int, ctx: _Context, budget: _Budget) -> None:
    """Check a greedy or lazy repeat ``{low,high}``.

    Args:
        av: ``(low, high, body)`` from the parser.
        follow: Characters that can come right after the repeat.
        ctx: Enclosing repeat context.
        budget: Running cost estimate.

    Returns:
        None

    Raises:
        UnsafeRegexError: If the repeat is nested, empty, or over budget.
    """
    low, high, body = av
    body_first, body_nullable = first(body)
    looping = high > 1
    if looping and body_nullable:
        raise UnsafeRegexError(EMPTY_LOOP)
    inner_follow = (body_first | follow) if looping else follow
    if low == high:
        # A fixed count is the same as writing the body that many times.
        _walk(body, inner_follow, replace(ctx, copies=ctx.copies * low), budget)
        return
    if body_nullable or body_first & follow:
        if ctx.in_loop:
            raise UnsafeRegexError(NESTED)
        if high > SMALL_REPEAT:
            budget.add_loop(ctx.copies)
        else:
            budget.add_choices(high - low + 1, ctx.copies)
    _walk(body, inner_follow, replace(ctx, in_loop=ctx.in_loop or looping), budget)


def _walk_branch(alternatives: Sequence[Any], follow: int, ctx: _Context, budget: _Budget) -> None:
    """Check an alternation ``a|b|...``.

    Args:
        alternatives: Parser sequences, one per alternative.
        follow: Characters that can come right after the alternation.
        ctx: Enclosing repeat context.
        budget: Running cost estimate.

    Returns:
        None

    Raises:
        UnsafeRegexError: If overlapping alternatives repeat or exceed the budget.
    """
    seen, empties, overlapping = NONE, 0, False
    for alt in alternatives:
        item, nullable = first(alt)
        if nullable:
            item |= follow
            empties += 1
        overlapping = overlapping or bool(item & seen) or empties > 1
        seen |= item
    if overlapping:
        if ctx.in_loop:
            raise UnsafeRegexError(BRANCH)
        budget.add_choices(len(alternatives), ctx.copies)
    for alt in alternatives:
        _walk(alt, follow, ctx, budget)


def regex_problem(body: str) -> str | None:
    """Explain why a regular expression is unsafe to full-match, or return ``None``.

    The pattern is analysed as ``re`` full-matches it case-insensitively,
    which is how vakr uses every ``re:`` pattern.

    Args:
        body: Regular expression without the ``re:`` prefix.

    Returns:
        str | None: Reason the pattern is rejected, or ``None`` when safe.

    Raises:
        None
    """
    try:
        tree = _SRE.parse(body, int(re.IGNORECASE))
        _walk(tree, NONE, _Context(), _Budget())
    except re.error as exc:
        return f"invalid regular expression: {exc.msg}"
    except RecursionError:
        return "regular expression is nested too deeply"
    except UnsafeRegexError as exc:
        return str(exc)
    return None
