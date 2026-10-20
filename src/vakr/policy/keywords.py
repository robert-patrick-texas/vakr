"""Description keywords: tokenising, validation, and the effective list (META-012, E.15).

A description is split into tokens on every character that is not a letter
or digit, so ``WAN-ISP_Carrier`` gives ``WAN``, ``ISP``, ``Carrier``. A
keyword is either one such word, matched case-insensitively (``ISP``), one
word matched case-sensitively (``exact:ISP``), or a ``re:`` pattern that
must match one whole token (``re:CKT[0-9]+``) under the POLICY-005 bounds.
A keyword containing other characters could never match a token, so it is
rejected rather than silently ignored.

The packaged keywords are ISP, WAN, and UPLINK (Appendix E.15). The
configuration loader appends operator keywords to them, or uses only the
operator's when ``use_default_keywords = false``.

Dependencies:
    vakr.config: The effective configuration and the packaged catalog.
    vakr.policy.patterns: Keyword patterns.

Safety:
    Pure apart from reading the packaged catalog once (cached).

Example:
    >>> tokenize("ISP: Carrier-X 1G ckt ABC12345")
    ('ISP', 'Carrier', 'X', '1G', 'ckt', 'ABC12345')
"""

import re
from dataclasses import dataclass
from functools import cache

from vakr.config import project
from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import InterestClass
from vakr.policy.errors import PolicyError, PolicyIssue, raise_if_any
from vakr.policy.patterns import Pattern, PatternKind, compile_pattern
from vakr.policy.reader import TableReader

KEYWORDS_KEY = "interface_classification.keywords"
USE_DEFAULTS_KEY = "interface_classification.use_default_keywords"
MAX_KEYWORDS = 256
"""Most keywords one configuration may define."""

_SPLIT = re.compile(r"[\W_]+")
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_ENTRY_KEYS = ("keyword", "tag", "interest", "wan_circuit", "sticky")


def tokenize(description: str) -> tuple[str, ...]:
    """Split a description on every character that is not a letter or digit.

    Args:
        description: Interface description, such as ``[WAN] MPLS primary``.

    Returns:
        tuple[str, ...]: Non-empty tokens in order, such as ``("WAN", "MPLS", "primary")``.

    Raises:
        None
    """
    return tuple(token for token in _SPLIT.split(description) if token)


@dataclass(frozen=True, slots=True)
class Keyword:
    """One validated description keyword and what it implies.

    Attributes:
        text: The keyword as configured, such as ``ISP`` or ``re:CKT[0-9]+``.
        pattern: Compiled pattern, applied to one token at a time.
        tag: Tag added when it matches.
        interest: Interest class it implies, or ``None``.
        wan_circuit: Whether it marks the interface as a WAN circuit.
        sticky: Whether it makes the interface sticky.
    """

    text: str
    pattern: Pattern
    tag: str
    interest: InterestClass | None = None
    wan_circuit: bool = False
    sticky: bool = False


@dataclass(frozen=True, slots=True)
class KeywordSet:
    """The effective keyword list, in configuration order.

    Attributes:
        keywords: Packaged keywords first (unless disabled), then the operator's.
        use_defaults: Value of ``interface_classification.use_default_keywords``.
    """

    keywords: tuple[Keyword, ...] = ()
    use_defaults: bool = True


def _keyword(entry: object, path: str, issues: list[PolicyIssue]) -> Keyword | None:
    """Validate one keyword table.

    Args:
        entry: The table as loaded.
        path: Dotted location of the entry.
        issues: List that problems are appended to.

    Returns:
        Keyword | None: The keyword, or ``None`` when it has problems.

    Raises:
        None
    """
    before = len(issues)
    reader = TableReader(entry, path, issues, _ENTRY_KEYS)
    text = reader.text("keyword", required=True)
    tag = reader.text("tag", required=True)
    interest = reader.choice("interest", [c.value for c in InterestClass])
    wan_circuit, sticky = reader.boolean("wan_circuit"), reader.boolean("sticky")
    pattern = None
    try:
        pattern = None if text is None else compile_pattern(text, reader.at("keyword"))
    except PolicyError as exc:
        issues.extend(exc.issues)
    if (
        pattern
        and pattern.kind is not PatternKind.REGEX
        and tokenize(pattern.body) != (pattern.body,)
    ):
        reader.problem("keyword", "a keyword is one word of letters and digits; use re: otherwise")
    if tag is not None and not _TAG.fullmatch(tag):
        reader.problem("tag", "use 1-64 letters, digits, '.', '_', ':' or '-'")
    if len(issues) > before or pattern is None or text is None or tag is None:
        return None
    level = InterestClass(interest) if interest else None
    return Keyword(text, pattern, tag, level, bool(wan_circuit), bool(sticky))


def parse_keywords(entries: object, path: str, use_defaults: bool = True) -> KeywordSet:
    """Validate a keyword list as loaded from configuration.

    Args:
        entries: List of keyword tables.
        path: Dotted location of the list, for error messages.
        use_defaults: Whether the list already includes the packaged keywords.

    Returns:
        KeywordSet: The validated keywords, in order.

    Raises:
        PolicyError: Listing every invalid entry with its path.
    """
    issues: list[PolicyIssue] = []
    if not isinstance(entries, list) or len(entries) > MAX_KEYWORDS:
        raise PolicyError([PolicyIssue(path, f"expected at most {MAX_KEYWORDS} keyword tables")])
    found = [kw for i, e in enumerate(entries) if (kw := _keyword(e, f"{path}[{i}]", issues))]
    raise_if_any(issues)
    return KeywordSet(tuple(found), use_defaults)


def keywords_from_config(config: EffectiveConfig) -> KeywordSet:
    """Return the effective keyword list from loaded configuration.

    The loader has already applied ``use_default_keywords``: with ``true``
    the list is the packaged keywords followed by the operator's, with
    ``false`` only the operator's (Appendix E.15).

    Args:
        config: Effective configuration from ``load_effective_config``.

    Returns:
        KeywordSet: Validated keywords.

    Raises:
        PolicyError: If a keyword is invalid or its ``re:`` pattern is unsafe.
    """
    use_defaults = bool(config.get(USE_DEFAULTS_KEY, True))
    return parse_keywords(config.get(KEYWORDS_KEY, []), KEYWORDS_KEY, use_defaults)


@cache
def packaged_keywords() -> KeywordSet:
    """Return the packaged keywords ISP, WAN, and UPLINK (Appendix E.15).

    Args:
        None

    Returns:
        KeywordSet: The catalog's default keyword list.

    Raises:
        PolicyError: If the packaged catalog's keywords are invalid (a bug).
    """
    option = project.catalog().option(KEYWORDS_KEY)
    return parse_keywords(option.default if option else [], f"packaged {KEYWORDS_KEY}")
