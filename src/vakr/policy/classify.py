"""Interface classification from description keywords (META-012, META-013, ADR-033).

NetOps already labels interfaces of interest in their descriptions
(``ifAlias``): ``ISP: Carrier-X 1G ckt ABC12345``, ``[WAN] MPLS primary``,
``UPLINK to core-sw1 Te1/0/1``. vakr reads those labels first: keywords
match whole tokens of the description case-insensitively (see
``keywords``), so ``WAN-ISP_Carrier`` yields ``WAN`` and ``ISP`` while
``SWANSEA`` yields nothing.

Each matching keyword adds its tag to the description-derived tags, which
are kept apart from tags added in vakr and follow the description when it
changes (META-013). A keyword may also imply interest, WAN circuit, or
sticky. When keywords disagree on interest, the later keyword in the
effective list wins, so operator keywords, appended after the packaged
ones (Appendix E.15), override them. Whether a keyword's implication takes
effect is decided by the engine: an explicit vakr annotation outranks it,
and it outranks policy rules.

Dependencies:
    vakr.policy.keywords: Tokenising and the effective keyword list.
    vakr.policy.facts: Interface annotations, for ``not_yet_classified``.

Safety:
    Pure. Descriptions longer than ``MAX_INPUT_LENGTH`` are not classified,
    which bounds the work per interface.

Example:
    >>> from vakr.policy.keywords import parse_keywords
    >>> kw = parse_keywords([{"keyword": "WAN", "tag": "wan", "interest": "primary"}], "k")
    >>> classify("WAN-ISP_Carrier", kw).tags, classify("SWANSEA", kw).tags
    (('wan',), ())
"""

from dataclasses import dataclass

from vakr.policy.facts import InterfaceFacts
from vakr.policy.keywords import Keyword, KeywordSet, packaged_keywords, tokenize
from vakr.policy.patterns import MAX_INPUT_LENGTH


@dataclass(frozen=True, slots=True)
class KeywordHit:
    """A keyword that matched, and the first token it matched.

    Attributes:
        keyword: The keyword.
        token: Token of the description it matched.
    """

    keyword: Keyword
    token: str


@dataclass(frozen=True, slots=True)
class Classification:
    """What one description says about its interface.

    Attributes:
        description: The description classified, or ``None`` when unknown.
        tokens: Its tokens.
        hits: Matching keywords, in keyword order.
        too_long: ``True`` when the description exceeded ``MAX_INPUT_LENGTH``
            and was not classified.
    """

    description: str | None
    tokens: tuple[str, ...] = ()
    hits: tuple[KeywordHit, ...] = ()
    too_long: bool = False

    @property
    def tags(self) -> tuple[str, ...]:
        """Description-derived tags, sorted and without duplicates.

        Args:
            None

        Returns:
            tuple[str, ...]: Tags such as ``("isp", "wan")``.

        Raises:
            None
        """
        return tuple(sorted({hit.keyword.tag for hit in self.hits}))

    @property
    def interest(self) -> KeywordHit | None:
        """The keyword that decides interest: the last one that sets it.

        Args:
            None

        Returns:
            KeywordHit | None: That hit, or ``None`` when no keyword sets interest.

        Raises:
            None
        """
        return next((h for h in reversed(self.hits) if h.keyword.interest is not None), None)

    def flag(self, name: str) -> KeywordHit | None:
        """Return the first keyword that sets ``wan_circuit`` or ``sticky``.

        Args:
            name: ``"wan_circuit"`` or ``"sticky"``.

        Returns:
            KeywordHit | None: That hit, or ``None`` when no keyword sets it.

        Raises:
            None
        """
        return next((h for h in self.hits if getattr(h.keyword, name)), None)


def classify(description: str | None, keywords: KeywordSet) -> Classification:
    """Classify an interface from its description.

    Args:
        description: ``ifAlias``, or ``None`` when it was never collected.
        keywords: Effective keyword list.

    Returns:
        Classification: Tokens, matching keywords, and implied annotations.

    Raises:
        None
    """
    if description is None:
        return Classification(None)
    if len(description) > MAX_INPUT_LENGTH:
        return Classification(description, too_long=True)
    tokens = tokenize(description)
    hits = []
    for keyword in keywords.keywords:
        token = next((t for t in tokens if keyword.pattern.matches(t)), None)
        if token is not None:
            hits.append(KeywordHit(keyword, token))
    return Classification(description, tokens, tuple(hits))


def not_yet_classified(facts: InterfaceFacts, keywords: KeywordSet | None = None) -> bool:
    """Whether an interface has neither a vakr annotation nor a keyword match (META-013).

    This backs the "not yet classified" filter that lets NetOps work through
    interfaces whose descriptions carry no keyword. An annotation means an
    explicit interest, sticky, or WAN-circuit value, or any tag added in vakr.

    Args:
        facts: The interface.
        keywords: Effective keyword list; ``None`` means the packaged keywords,
            so callers with operator configuration should pass theirs.

    Returns:
        bool: ``True`` when the operator still has to classify it.

    Raises:
        None
    """
    if facts.annotations.classifies:
        return False
    effective = keywords if keywords is not None else packaged_keywords()
    return not classify(facts.if_alias, effective).hits
