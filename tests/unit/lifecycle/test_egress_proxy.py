"""EGRESS-T03: proxy precedence (config, environment) and ``no_proxy`` bypass (EGRESS-001).

Dependencies:
    pytest: Parametrized cases.

Example:
    $ uv run pytest tests/unit/lifecycle/test_egress_proxy.py
"""

import pytest

from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.egress.proxy import (
    ProxyEndpoint,
    bypasses,
    parse_proxy_url,
    resolve_policy,
    split_no_proxy,
)


def test_config_proxy_wins_over_environment() -> None:
    """EGRESS-T03: a configured ``https_proxy`` overrides ``HTTPS_PROXY``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    env = {"HTTPS_PROXY": "http://env-proxy:8080", "https_proxy": "http://lower:1"}
    policy = resolve_policy("http://cfg-proxy:3128", [], env)
    assert policy.https_proxy == ProxyEndpoint("cfg-proxy", 3128)
    assert policy.source == "config"


def test_environment_fallback_prefers_upper_case() -> None:
    """EGRESS-T03: without config, ``HTTPS_PROXY`` is used before ``https_proxy``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    both = resolve_policy("", None, {"HTTPS_PROXY": "http://upper:1", "https_proxy": "http://l:2"})
    assert both.https_proxy == ProxyEndpoint("upper", 1)
    assert both.source == "environment"
    lower = resolve_policy(None, None, {"https_proxy": "lower.example:3128"})
    assert lower.https_proxy == ProxyEndpoint("lower.example", 3128)
    none = resolve_policy("  ", [], {})
    assert none.https_proxy is None
    assert none.source == "none"
    assert none.proxy_for("api.github.com") is None


def test_no_proxy_config_overrides_environment_independently() -> None:
    """EGRESS-T03: ``no_proxy`` resolves on its own; config beats ``NO_PROXY``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    env = {"HTTPS_PROXY": "http://p:3128", "NO_PROXY": "github.com", "no_proxy": "ignored"}
    from_env = resolve_policy("", [], env)
    assert from_env.no_proxy == ("github.com",)
    assert from_env.proxy_for("api.github.com") is None
    from_cfg = resolve_policy("", ["corp.example"], env)
    assert from_cfg.no_proxy == ("corp.example",)
    assert from_cfg.proxy_for("api.github.com") == ProxyEndpoint("p", 3128)
    lower = resolve_policy("", None, {"https_proxy": "http://p:1", "no_proxy": "a.example"})
    assert lower.no_proxy == ("a.example",)


@pytest.mark.parametrize(
    ("host", "port", "entries", "expected"),
    [
        ("api.github.com", 443, ("github.com",), True),
        ("github.com", 443, (".github.com",), True),
        ("notgithub.com", 443, ("github.com",), False),
        ("x.corp.example", 443, ("*.corp.example",), True),
        ("anything", 443, ("*",), True),
        ("10.1.2.3", 443, ("10.0.0.0/8",), True),
        ("11.1.2.3", 443, ("10.0.0.0/8",), False),
        ("name.example", 443, ("10.0.0.0/8",), False),
        ("10.1.2.3", 443, ("10.1.2.3",), True),
        ("[::1]", 443, ("::1",), True),
        ("::1", 443, ("[::1]:443",), True),
        ("::1", 8443, ("[::1]:443",), False),
        ("svc.example", 8443, ("svc.example:8443",), True),
        ("svc.example", 443, ("svc.example:8443",), False),
        ("Host.Example.", 443, ("host.example",), True),
        ("host.example", 443, (".",), False),
        ("host.example", 443, ("bad/cidr",), False),
    ],
)
def test_no_proxy_matching(host: str, port: int, entries: tuple[str, ...], expected: bool) -> None:
    """EGRESS-T03: bypass rules match hosts, domains, IPs, CIDRs, and ports as documented.

    Args:
        host: Destination host.
        port: Destination port.
        entries: Normalized ``no_proxy`` entries.
        expected: Whether the destination bypasses the proxy.

    Returns:
        None

    Raises:
        None
    """
    assert bypasses(host, port, entries) is expected


def test_split_no_proxy_normalizes_lists_and_strings() -> None:
    """``no_proxy`` accepts a comma string or a list and drops blanks.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert split_no_proxy(" A.example , ,10.0.0.0/8") == ("a.example", "10.0.0.0/8")
    assert split_no_proxy(["X", " "]) == ("x",)
    assert split_no_proxy(None) == ()


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://user:pw@proxy:3128", "credentials"),
        ("https://proxy:3128", "scheme"),
        ("http://proxy", "explicit port"),
        ("http://proxy:notaport", "invalid port"),
        ("http://:3128", "explicit port"),
    ],
)
def test_bad_proxy_urls_are_rejected(url: str, reason: str) -> None:
    """EGRESS-001: credentials, TLS-to-proxy, and missing ports are configuration errors.

    Args:
        url: Candidate proxy URL.
        reason: Text expected in the error.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(EgressError, match=reason) as exc:
        parse_proxy_url(url)
    assert exc.value.step == "config"
    assert "pw" not in str(exc.value)


def test_endpoint_renders_ipv6_in_brackets() -> None:
    """Proxy endpoints render as credential-free URLs.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert str(parse_proxy_url("http://[2001:db8::1]:3128")) == "http://[2001:db8::1]:3128"
    assert str(ProxyEndpoint("proxy.corp", 8080)) == "http://proxy.corp:8080"
