"""Schedule hashing, collector naming, and the four phase/order modes (SCHED-T01, SCHED-T02).

The golden vectors in ``golden_phase_vectors.json`` pin E.11 hashing and
every offset rule, so a future Go collector can prove it schedules exactly
like this one. Two vectors are the published SHA-256 values of ``""`` and
``"abc"``, which check the byte order independently of this code.

Dependencies:
    pytest, json: Tables and the golden file.
    vakr.scheduler.hashing, phase: The code under test.

Example:
    $ uv run pytest tests/unit/scheduler/test_sched_hashing.py
"""

import json
from pathlib import Path
from uuid import UUID

import pytest

from vakr.scheduler.hashing import hash64, resolve_collector_name, short_hostname
from vakr.scheduler.phase import OrderMode, PhaseMode, PhaseRule, first_slot_us

GOLDEN = json.loads((Path(__file__).parent / "golden_phase_vectors.json").read_text("utf-8"))
DEVICES = [UUID(f"0190a3c4-0000-7000-8000-{i:012x}") for i in range(1, 21)]
MINUTE_US = 60_000_000


def test_hash64_matches_published_sha256_prefixes() -> None:
    """E.11: first 8 bytes of SHA-256, big-endian (FIPS 180-2 test vectors).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert hash64("") == 0xE3B0C44298FC1C14
    assert hash64("abc") == 0xBA7816BF8F01CFEA


@pytest.mark.parametrize("vector", GOLDEN["hash64"], ids=lambda v: repr(v["input"]))
def test_golden_hash64(vector: dict) -> None:
    """Each pinned input hashes to its pinned value (E.11).

    Args:
        vector: One golden ``hash64`` entry.

    Returns:
        None

    Raises:
        None
    """
    assert hash64(vector["input"]) == vector["value"] == int(vector["hex"], 16)


@pytest.mark.parametrize(
    "vector", GOLDEN["phase"], ids=lambda v: f"{v['collector']}@{v['interval_s']}"
)
def test_golden_collector_phase(vector: dict) -> None:
    """The hostname phase is ``hash64(collector) mod interval`` (SCHED-003).

    Args:
        vector: One golden ``phase`` entry.

    Returns:
        None

    Raises:
        None
    """
    rule = PhaseRule(vector["collector"])
    assert rule.collector_phase_us(vector["interval_s"]) == vector["phase_us"]


@pytest.mark.parametrize("vector", GOLDEN["offsets"])
def test_golden_offsets(vector: dict) -> None:
    """Every phase/order combination reproduces its pinned offset (SCHED-T02).

    Args:
        vector: One golden ``offsets`` entry.

    Returns:
        None

    Raises:
        None
    """
    rule = PhaseRule(
        vector["collector"], PhaseMode(vector["phase"]), OrderMode(vector["order"]),
        vector["fixed_phase_us"],
    )  # fmt: skip
    device = UUID(vector["device"])
    assert rule.order_input(device) == vector["order_input"]
    assert rule.offset_us(device, vector["interval_s"]) == vector["offset_us"]


def test_hostname_phase_ignores_dns_suffix_and_case() -> None:
    """SCHED-T01: the same short hostname gives the same phase; the DNS suffix is excluded.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    names = {
        resolve_collector_name("", h) for h in ("vakr-east", "Vakr-East.example.net", "vakr-east.")
    }
    assert names == {"vakr-east"}
    phases = {PhaseRule(n).collector_phase_us(60) for n in names}
    assert phases == {PhaseRule("vakr-east").collector_phase_us(60)}
    assert PhaseRule("vakr-west").collector_phase_us(60) not in phases


def test_configured_collector_name_is_used_verbatim() -> None:
    """SCHED-001: a configured name wins and keeps its case; bad names are refused.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert resolve_collector_name(" East-1 ", "host.example.net") == "East-1"
    with pytest.raises(ValueError, match="collector_name"):
        resolve_collector_name("east/1", "host")
    with pytest.raises(ValueError, match="usable first label"):
        short_hostname(".example.net")


def _offsets(rule: PhaseRule) -> list[int]:
    """Return 60 s offsets of the test devices under a rule.

    Args:
        rule: The phase rule.

    Returns:
        list[int]: Offsets in device order.

    Raises:
        None
    """
    return [rule.offset_us(d, 60) for d in DEVICES]


def _shift(a: list[int], b: list[int]) -> set[int]:
    """Return the distinct per-device offset differences between two schedules.

    Args:
        a: Offsets under one rule.
        b: Offsets under another.

    Returns:
        set[int]: ``(b - a) mod 60 s`` for each device.

    Raises:
        None
    """
    return {(y - x) % MINUTE_US for x, y in zip(a, b, strict=True)}


def test_phase_and_order_are_independent_controls() -> None:
    """SCHED-T02, SCHED-002..006: phase shifts everyone; order rearranges devices.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    east, west = PhaseRule("vakr-east"), PhaseRule("vakr-west")
    # Common order: two collectors differ only by phase, so every device moves by one constant.
    assert len(_shift(_offsets(east), _offsets(west))) == 1
    fixed = PhaseRule("vakr-east", PhaseMode.FIXED, fixed_phase_us=17_500_000)
    assert _shift(_offsets(east), _offsets(fixed)) == {
        (17_500_000 - east.collector_phase_us(60)) % MINUTE_US
    }
    # Hashed order: each collector has its own arrangement, so the shift is not constant.
    hashed_e = PhaseRule("vakr-east", order=OrderMode.HASHED)
    hashed_w = PhaseRule("vakr-west", order=OrderMode.HASHED)
    assert len(_shift(_offsets(hashed_e), _offsets(hashed_w))) == len(DEVICES)
    # Fixed phase with hashed order still keys order on the collector name (SCHED-005).
    fixed_hashed = PhaseRule("vakr-east", PhaseMode.FIXED, OrderMode.HASHED, 17_500_000)
    assert len(_shift(_offsets(hashed_e), _offsets(fixed_hashed))) == 1


def test_fixed_phase_wraps_within_each_interval() -> None:
    """SCHED-004: a fixed phase longer than an interval is taken modulo it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    rule = PhaseRule("x", PhaseMode.FIXED, fixed_phase_us=75_000_000)
    assert rule.collector_phase_us(60) == 15_000_000
    assert rule.collector_phase_us(300) == 75_000_000
    with pytest.raises(ValueError, match="collector name"):
        PhaseRule("")
    with pytest.raises(ValueError, match="negative"):
        PhaseRule("x", fixed_phase_us=-1)


@pytest.mark.parametrize(
    ("offset", "not_before", "expected"),
    [
        (5, 0, 5),
        (5, 5, 5),
        (5, 6, MINUTE_US + 5),
        (0, MINUTE_US * 3, MINUTE_US * 3),
        (59_999_999, MINUTE_US * 10 + 1, MINUTE_US * 10 + 59_999_999),
    ],
)
def test_first_slot_is_on_the_grid_and_not_early(
    offset: int, not_before: int, expected: int
) -> None:
    """Slots are ``k * interval + offset``; the first is the earliest at or after the bound.

    Args:
        offset: Device offset.
        not_before: Earliest acceptable slot.
        expected: Expected slot.

    Returns:
        None

    Raises:
        None
    """
    assert first_slot_us(offset, MINUTE_US, not_before) == expected
