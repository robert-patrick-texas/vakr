r"""First-character sets of regex parse-tree items, for backtracking analysis.

``regex_safety`` decides whether a repeat or an alternation gives the regex
engine a real choice: whether the characters that can start one part can
also start the part after it. This module computes those character sets
from CPython's parse tree (``re._parser``) as bitmasks: one bit per ASCII
character plus four bits for the disjoint kinds of non-ASCII character the
way CPython classifies them (decimal digit, other alphanumeric, space, and
the rest), so that ``\w+\s`` and ``\D+\d`` are still seen as unambiguous.
Literals are case-folded because vakr matches
``re:`` patterns case-insensitively, including CPython's non-ASCII case
matches (``k`` and the Kelvin sign, ``s`` and long s, ``i`` and dotless i).

Dependencies:
    string: ASCII character classes.

Safety:
    Every set is an over-approximation (a class the analysis cannot read
    counts as "any character"), so overlaps are found too often, never too
    rarely. Unknown parse nodes raise ``UnsafeRegexError``.

Example:
    >>> fold(ord("a")) == (1 << ord("a")) | (1 << ord("A"))
    True
    >>> first_item("ANY", None) == (ANY, False)
    True
"""

import string
from collections.abc import Callable, Mapping, Sequence
from typing import Any

ASCII = (1 << 128) - 1
OTHER_DIGIT = 1 << 128
r"""Marker for non-ASCII decimal digits, which ``\d`` and ``\w`` match."""
OTHER_ALNUM = 1 << 129
r"""Marker for other non-ASCII letters and numbers, which ``\w`` matches."""
OTHER_SPACE = 1 << 130
r"""Marker for non-ASCII whitespace, which ``\s`` matches."""
OTHER_REST = 1 << 131
"""Marker for every other non-ASCII character."""
OTHER = OTHER_DIGIT | OTHER_ALNUM | OTHER_SPACE | OTHER_REST
ANY = ASCII | OTHER
NONE = 0

REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT"})
"""Greedy and lazy repeats; possessive repeats never backtrack."""

ZERO_WIDTH = frozenset({"AT", "ASSERT", "ASSERT_NOT"})
"""Anchors and lookarounds, which consume nothing."""

BACKREF = "backreferences and conditional groups are not allowed"


class UnsafeRegexError(Exception):
    """A pattern is rejected; the message says why in operator terms."""


def bits(chars: str) -> int:
    """Return the bitmask of some ASCII characters.

    Args:
        chars: ASCII characters.

    Returns:
        int: One bit per character code.

    Raises:
        None
    """
    mask = NONE
    for char in chars:
        mask |= 1 << ord(char)
    return mask


_DIGITS = bits(string.digits)
_SPACES = bits(" \t\n\r\f\v\x1c\x1d\x1e\x1f")
_WORD = bits(string.ascii_letters + string.digits + "_")
_UNICODE_CASES = bits("iksIKS")
# Category name: (ASCII characters it matches, non-ASCII kinds it matches).
# A category matches its non-ASCII kinds entirely, so a negated class such
# as [^\s] can drop that kind instead of keeping it to be safe.
_CATEGORIES = {
    "CATEGORY_DIGIT": (_DIGITS, OTHER_DIGIT),
    "CATEGORY_NOT_DIGIT": (ASCII & ~_DIGITS, OTHER & ~OTHER_DIGIT),
    "CATEGORY_SPACE": (_SPACES, OTHER_SPACE),
    "CATEGORY_NOT_SPACE": (ASCII & ~_SPACES, OTHER & ~OTHER_SPACE),
    "CATEGORY_WORD": (_WORD, OTHER_DIGIT | OTHER_ALNUM),
    "CATEGORY_NOT_WORD": (ASCII & ~_WORD, OTHER_SPACE | OTHER_REST),
}


def fold(code: int) -> int:
    """Return the set one literal matches case-insensitively.

    Args:
        code: Character code.

    Returns:
        int: ASCII bits for every case variant, plus the kind marker of
        each non-ASCII variant.

    Raises:
        None
    """
    char = chr(code)
    if code < 128:
        mask = (1 << ord(char.lower())) | (1 << ord(char.upper()))
        return mask | (OTHER_ALNUM if mask & _UNICODE_CASES else NONE)
    kind = OTHER_REST
    if char.isspace():
        kind = OTHER_SPACE
    elif char.isdecimal():
        kind = OTHER_DIGIT
    elif char.isalnum():
        kind = OTHER_ALNUM
    for variant in (char.lower(), char.upper()):
        if len(variant) == 1 and ord(variant) < 128:
            kind |= fold(ord(variant))
    return kind


def class_bits(items: Sequence[Any]) -> int:
    """Return the set a ``[...]`` class can match.

    Args:
        items: Parser members of the class.

    Returns:
        int: Bitmask; a class containing a member the analysis cannot read
        counts as any character.

    Raises:
        None
    """
    negate, mask, covered = False, NONE, NONE
    for op, av in items:
        if op.name == "NEGATE":
            negate = True
        elif op.name == "LITERAL":
            mask |= fold(av)
        elif op.name == "RANGE":
            low, high = av
            mask |= OTHER if high >= 128 else NONE
            for code in range(low, min(high, 127) + 1):
                mask |= fold(code)
        elif op.name == "CATEGORY" and av.name in _CATEGORIES:
            ascii_part, kinds = _CATEGORIES[av.name]
            mask, covered = mask | ascii_part | kinds, covered | kinds
        else:
            return ANY
    # A complement keeps every non-ASCII kind the class does not match
    # entirely, which over-approximates and so only makes overlaps likelier.
    return (ASCII & ~mask) | (OTHER & ~covered) if negate else mask


_LEAVES: Mapping[str, Callable[[Any], int]] = {
    "LITERAL": fold,
    "NOT_LITERAL": lambda code: (ASCII & ~fold(code)) | OTHER,
    "ANY": lambda _av: ANY,
    "IN": class_bits,
}


def first(seq: Sequence[Any]) -> tuple[int, bool]:
    """Return the characters that can start ``seq`` and whether it can match nothing.

    Args:
        seq: Parser items in order.

    Returns:
        tuple[int, bool]: First-character bitmask and nullability.

    Raises:
        UnsafeRegexError: If ``seq`` contains an unsupported construct.
    """
    mask = NONE
    for op, av in seq:
        item, nullable = first_item(op.name, av)
        mask |= item
        if not nullable:
            return mask, False
    return mask, True


def first_item(name: str, av: Any) -> tuple[int, bool]:
    """Return the first-character set and nullability of one parser item.

    Args:
        name: Parser opcode name, such as ``LITERAL`` or ``MAX_REPEAT``.
        av: The opcode's argument.

    Returns:
        tuple[int, bool]: First-character bitmask and nullability.

    Raises:
        UnsafeRegexError: For backreferences, conditionals, and opcodes this
            module does not know.
    """
    if name in _LEAVES:
        return _LEAVES[name](av), False
    if name in ZERO_WIDTH:
        return NONE, True
    if name == "BRANCH":
        firsts = [first(alt) for alt in av[1]]
        mask = NONE
        for item, _nullable in firsts:
            mask |= item
        return mask, any(nullable for _item, nullable in firsts)
    if name in REPEATS or name == "POSSESSIVE_REPEAT":
        item, nullable = first(av[2])
        return item, nullable or av[0] == 0
    if name in {"SUBPATTERN", "ATOMIC_GROUP"}:
        return first(av[3] if name == "SUBPATTERN" else av)
    raise UnsafeRegexError(
        BACKREF if name.startswith("GROUPREF") else f"unsupported construct {name}"
    )
