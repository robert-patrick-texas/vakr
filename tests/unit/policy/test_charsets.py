"""CFG-005: first-character sets used by the regex safety analysis.

Dependencies:
    pytest: Parametrised cases.
    vakr.policy.charsets: The module under test.

Example:
    $ uv run pytest tests/unit/policy/test_charsets.py
"""

from types import SimpleNamespace

import pytest

from vakr.policy.charsets import (
    ANY,
    ASCII,
    OTHER_ALNUM,
    OTHER_DIGIT,
    OTHER_REST,
    OTHER_SPACE,
    bits,
    class_bits,
    fold,
)


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (0x0663, OTHER_DIGIT),  # Arabic-Indic digit three: \d and \w
        (0x00E9, OTHER_ALNUM),  # e acute: \w only
        (0x3000, OTHER_SPACE),  # ideographic space: \s
        (0x2014, OTHER_REST),  # em dash: none of them
    ],
)
def test_non_ascii_characters_get_their_cpython_kind(code: int, kind: int) -> None:
    """Non-ASCII literals are classified the way CPython's digit, word, and space classes see them.

    Args:
        code: A non-ASCII character code.
        kind: The expected marker bit.

    Returns:
        None

    Raises:
        None
    """
    assert fold(code) & ~ASCII == kind


def test_case_folding_reaches_across_ascii() -> None:
    """``k`` also matches the Kelvin sign, and the Kelvin sign matches ``k`` and ``K``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert fold(ord("k")) == bits("kK") | OTHER_ALNUM
    assert fold(0x212A) == bits("kK") | OTHER_ALNUM
    assert fold(ord("a")) == bits("aA")


def test_unknown_class_members_count_as_any_character() -> None:
    """A class member the analysis cannot read makes the class match anything.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    negate, unknown = SimpleNamespace(name="NEGATE"), SimpleNamespace(name="FUTURE_MEMBER")
    assert class_bits([(negate, None), (unknown, None)]) == ANY
