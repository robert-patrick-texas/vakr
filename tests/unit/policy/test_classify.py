"""META-012, META-013, Appendix E.15: description keywords and "not yet classified".

Dependencies:
    pytest: Parametrised cases and temporary configuration directories.
    vakr.policy.classify, vakr.policy.keywords: The modules under test.

Example:
    $ uv run pytest tests/unit/policy/test_classify.py
"""

from pathlib import Path
from typing import Any

import pytest

from tests.unit.policy.policy_fixtures import defaults, edge
from vakr.config.loader import EffectiveConfig, load_effective_config
from vakr.domain.enums import InterestClass
from vakr.policy.classify import classify, not_yet_classified
from vakr.policy.errors import PolicyError
from vakr.policy.facts import Annotations
from vakr.policy.keywords import (
    MAX_KEYWORDS,
    KeywordSet,
    keywords_from_config,
    parse_keywords,
    tokenize,
)


@pytest.mark.parametrize(
    ("description", "tokens"),
    [
        ("WAN-ISP_Carrier", ("WAN", "ISP", "Carrier")),
        ("ISP: Carrier-X 1G ckt ABC12345", ("ISP", "Carrier", "X", "1G", "ckt", "ABC12345")),
        ("[WAN] MPLS primary", ("WAN", "MPLS", "primary")),
        ("UPLINK to core-sw1 Te1/0/1", ("UPLINK", "to", "core", "sw1", "Te1", "0", "1")),
        ("  user  port ", ("user", "port")),
        ("", ()),
        ("---", ()),
    ],
)
def test_tokens_split_on_every_non_alphanumeric(description: str, tokens: tuple[str, ...]) -> None:
    """META-012: punctuation, spaces, and underscores all separate tokens.

    Args:
        description: ``ifAlias`` text.
        tokens: Expected tokens.

    Returns:
        None

    Raises:
        None
    """
    assert tokenize(description) == tokens


@pytest.mark.parametrize(
    ("description", "tags"),
    [
        ("WAN-ISP_Carrier", ("isp", "wan")),
        ("SWANSEA", ()),
        ("ISP: Carrier-X 1G ckt ABC12345", ("isp",)),
        ("[WAN] MPLS primary", ("wan",)),
        ("UPLINK to core-sw1 Te1/0/1", ("uplink",)),
        ("uplink to core-sw1", ("uplink",)),  # case-insensitive
        ("Uplinks to core", ()),  # whole tokens only
        ("WANISP", ()),
        ("user port", ()),
        ("", ()),
    ],
)
def test_default_keywords_match_whole_tokens(description: str, tags: tuple[str, ...]) -> None:
    """Appendix E.15 defaults ISP, WAN, UPLINK match whole tokens case-insensitively.

    Args:
        description: ``ifAlias`` text.
        tags: Expected description-derived tags.

    Returns:
        None

    Raises:
        None
    """
    assert classify(description, defaults()).tags == tags


def test_default_keywords_imply_the_e15_annotations() -> None:
    """ISP and WAN are primary WAN circuits; UPLINK is primary but not a WAN circuit.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    implied = {
        kw.text: (kw.tag, kw.interest, kw.wan_circuit, kw.sticky) for kw in defaults().keywords
    }
    assert implied == {
        "ISP": ("isp", InterestClass.PRIMARY, True, False),
        "WAN": ("wan", InterestClass.PRIMARY, True, False),
        "UPLINK": ("uplink", InterestClass.PRIMARY, False, False),
    }
    found = classify("WAN-ISP_Carrier", defaults())
    assert [(h.keyword.text, h.token) for h in found.hits] == [("ISP", "ISP"), ("WAN", "WAN")]
    assert found.flag("wan_circuit") is not None
    assert found.flag("sticky") is None
    assert classify("UPLINK", defaults()).flag("wan_circuit") is None


def _config(tmp_path: Path, body: str) -> EffectiveConfig:
    """Load the effective configuration from one ``config.toml``.

    Args:
        tmp_path: Directory to hold the file.
        body: TOML after the ``config_version`` line.

    Returns:
        EffectiveConfig: The merged configuration.

    Raises:
        ConfigError: If the configuration is invalid.
    """
    (tmp_path / "config.toml").write_text(f"config_version = 1\n{body}", encoding="utf-8")
    return load_effective_config(tmp_path)


def test_operator_keywords_extend_the_defaults(tmp_path: Path) -> None:
    """Section 27.9: a TRANSIT keyword is added after ISP, WAN, and UPLINK.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    config = _config(
        tmp_path,
        '[[interface_classification.keywords]]\nkeyword = "TRANSIT"\ntag = "transit"\n'
        'interest = "primary"\n',
    )
    keywords = keywords_from_config(config)
    assert [kw.text for kw in keywords.keywords] == ["ISP", "WAN", "UPLINK", "TRANSIT"]
    assert keywords.use_defaults
    assert classify("TRANSIT via Carrier-Y", keywords).tags == ("transit",)


def test_use_default_keywords_false_replaces_the_defaults(tmp_path: Path) -> None:
    """Appendix E.15: with the switch off only the operator's keywords apply.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    config = _config(
        tmp_path,
        "[interface_classification]\nuse_default_keywords = false\n"
        '[[interface_classification.keywords]]\nkeyword = "re:CKT[0-9]+"\ntag = "circuit"\n'
        "wan_circuit = true\n",
    )
    keywords = keywords_from_config(config)
    assert ([kw.text for kw in keywords.keywords], keywords.use_defaults) == (
        ["re:CKT[0-9]+"],
        False,
    )
    found = classify("ISP ckt CKT0042", keywords)
    assert (found.tags, found.hits[0].token) == (("circuit",), "CKT0042")
    assert classify("ISP ckt CKT", keywords).tags == ()  # re: must match a whole token


def test_exact_keywords_are_case_sensitive() -> None:
    """``exact:`` keywords compare the token byte for byte.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    keywords = parse_keywords([{"keyword": "exact:DIA", "tag": "dia"}], "k")
    assert classify("DIA circuit", keywords).tags == ("dia",)
    assert classify("dia circuit", keywords).tags == ()


def test_later_keyword_decides_interest() -> None:
    """When keywords disagree, the later one (an operator keyword) sets interest.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    extra = [{"keyword": "BACKUP", "tag": "backup", "interest": "secondary"}]
    keywords = KeywordSet(defaults().keywords + parse_keywords(extra, "k").keywords)
    found = classify("WAN backup via LTE", keywords)
    assert found.tags == ("backup", "wan")
    assert found.interest is not None
    assert found.interest.keyword.interest is InterestClass.SECONDARY


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ({"tag": "x"}, "k[0].keyword: required"),
        ({"keyword": "WAN-ISP", "tag": "x"}, "one word of letters and digits"),
        ({"keyword": "UP*", "tag": "x"}, "one word of letters and digits"),
        ({"keyword": "exact:A B", "tag": "x"}, "one word"),
        ({"keyword": "re:(a+)+", "tag": "x"}, "k[0].keyword: nested quantifier"),
        ({"keyword": "re:" + "x" * 300, "tag": "x"}, "the limit is 256"),
        ({"keyword": "ISP"}, "k[0].tag: required"),
        ({"keyword": "ISP", "tag": "has space"}, "k[0].tag: use 1-64"),
        ({"keyword": "ISP", "tag": "isp", "interest": "high"}, "k[0].interest: expected one of"),
        ({"keyword": "ISP", "tag": "isp", "colour": "red"}, "k[0].colour: unknown key"),
    ],
)
def test_invalid_keywords_fail_with_their_path(entry: dict[str, Any], problem: str) -> None:
    """POLICY-005: unsafe or impossible keywords fail validation with a field path.

    Args:
        entry: Invalid keyword table.
        problem: Expected text of the problem.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(PolicyError) as caught:
        parse_keywords([entry], "k")
    assert any(problem in str(issue) for issue in caught.value.issues)


def test_keyword_list_is_bounded() -> None:
    """More than ``MAX_KEYWORDS`` entries, or a non-list, is refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(PolicyError, match="at most 256"):
        parse_keywords([{"keyword": "A", "tag": "a"}] * (MAX_KEYWORDS + 1), "k")
    with pytest.raises(PolicyError):
        parse_keywords({"keyword": "A"}, "k")


def test_unknown_and_long_descriptions_are_not_classified() -> None:
    """A description never collected has no tokens; an over-long one is skipped.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert classify(None, defaults()).hits == ()
    long = classify("WAN " * 200, defaults())
    assert long.too_long
    assert long.tags == ()


def test_not_yet_classified_needs_no_annotation_and_no_keyword() -> None:
    """META-013: the filter lists interfaces with neither a vakr annotation nor a keyword.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert not_yet_classified(edge("GigabitEthernet1/0/7", "user port"))
    assert not_yet_classified(edge("Vlan100", ""), defaults())
    assert not not_yet_classified(edge("GigabitEthernet0/0/0"))  # ISP keyword
    tagged = edge("GigabitEthernet1/0/7", "user port", annotations=Annotations(tags=("printers",)))
    assert not not_yet_classified(tagged)
    flagged = edge("Loopback0", "", annotations=Annotations(sticky=False))
    assert not not_yet_classified(flagged)
    assert not_yet_classified(edge("GigabitEthernet0/0/0"), KeywordSet())
