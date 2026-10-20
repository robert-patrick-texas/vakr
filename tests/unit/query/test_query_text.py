"""Operator formats and words: rates, ages, uptimes, times, quality flags (18.3, TIME-004).

Dependencies:
    pytest; vakr.query.text, vakr.query.labels.

Example:
    $ uv run pytest tests/unit/query/test_query_text.py
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from vakr.config.loader import EffectiveConfig
from vakr.domain.enums import IfAdminStatus, IfOperStatus
from vakr.domain.quality import Quality
from vakr.query.labels import QUALITY_WORDS, if_state_text, quality_words
from vakr.query.settings import QuerySettings, refresh_from_config, settings_from_config
from vakr.query.text import (
    age_text,
    bits_per_second,
    display_zone,
    local_time,
    milliseconds,
    packets_per_second,
    utc_iso,
)


@pytest.mark.parametrize(
    ("bps", "text"),
    [(0, "0 b/s"), (950, "950 b/s"), (999.4, "999 b/s"), (999.6, "1.00 kb/s"),
     (12_340, "12.3 kb/s"), (123_456_789, "123 Mb/s"), (10e9, "10.0 Gb/s"), (4e12, "4.00 Tb/s"),
     (5e15, "5000 Tb/s")],
)  # fmt: skip
def test_bit_rates_use_si_prefixes(bps: float, text: str) -> None:
    """A measured zero prints as zero; missing values never reach the formatter.

    Args:
        bps: Bits per second.
        text: Expected text.

    Returns:
        None

    Raises:
        None
    """
    assert bits_per_second(bps) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0 s"), (119.9, "119 s"), (120, "2 min"), (3599, "59 min"), (3600, "1 h"),
     (7500, "2 h 5 min"), (86_400, "1 d"), (90_000, "1 d 1 h"), (-5, "5 s ahead")],
)  # fmt: skip
def test_ages_are_coarse_and_future_times_are_shown(seconds: float, text: str) -> None:
    """E.17 #14: a future timestamp is shown as ahead, not hidden.

    Args:
        seconds: Age.
        text: Expected text.

    Returns:
        None

    Raises:
        None
    """
    assert age_text(seconds) == text


def test_small_formats() -> None:
    """Milliseconds, packets, interface states, and UTC timestamps (TIME-004).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert [milliseconds(v) for v in (900, 45_600, 812_000)] == ["0.90 ms", "45.6 ms", "812 ms"]
    assert packets_per_second(1_200) == "1.20 kpps"
    assert if_state_text(IfAdminStatus.UP, IfOperStatus.LOWER_LAYER_DOWN) == "up/lower-layer-down"
    assert if_state_text(None, None) == "?/?"
    when = datetime(2026, 10, 20, 14, 3, 22, tzinfo=timezone(timedelta(hours=-5)))
    assert utc_iso(when) == "2026-10-20T19:03:22Z"
    assert utc_iso(None) is None


def test_display_zone_follows_time_timezone() -> None:
    """TIME-002/003: an IANA zone, the host zone when empty, UTC when unknown.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    when = datetime(2026, 10, 20, 19, 0, tzinfo=UTC)
    assert local_time(when, display_zone("America/Chicago")) == "2026-10-20 14:00:00 CDT"
    assert local_time(when, display_zone("Not/AZone")) == "2026-10-20 19:00:00 UTC"
    assert display_zone("") is not None


def test_every_quality_flag_has_words() -> None:
    """18.3: each flag an observation can carry is explained in operator language.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    named = {flag for flag in Quality if flag is not Quality.NONE}
    assert named == set(QUALITY_WORDS)
    assert quality_words(Quality.RETRIED | Quality.PARTIAL_RESPONSE) == (
        "SNMP reply was partial",
        "SNMP needed retries",
    )


def test_settings_come_from_configuration() -> None:
    """E.25, E.1, E.29: defaults, then operator values.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert settings_from_config(EffectiveConfig({})) == QuerySettings()
    assert refresh_from_config(EffectiveConfig({})) == 10.0
    custom = EffectiveConfig(
        {
            "polling": {"default_interval": "2m", "reachability_strategy": "snmp_only"},
            "icmp": {"impaired_rtt_ms": 150.0},
            "time": {"timezone": "Europe/Berlin"},
            "tui": {"refresh_interval": "30s"},
        }
    )
    s = settings_from_config(custom)
    assert (s.default_interval_s, s.strategy.value, s.timezone) == (
        120,
        "snmp_only",
        "Europe/Berlin",
    )
    assert s.icmp_policy.impaired_rtt_ms == 150.0
    assert refresh_from_config(custom) == 30.0
