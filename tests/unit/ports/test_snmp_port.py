"""SNMP-T11 and SNMP-019: the SNMP port's values, targets, settings, and results validate.

Targets, OIDs, ports, address families, source addresses, and contexts are
normalised and checked before any request is built (SNMP-016, SNMP-019,
blueprint 15.5). Results count effort and earn the SNMP-017/SNMP-018 flags.

Dependencies:
    pytest: Parametrised cases.
    vakr.ports.snmp: The port types.

Example:
    $ uv run pytest tests/unit/ports/test_snmp_port.py
"""

import pytest

from vakr.domain.enums import AddressFamily, SnmpVersion
from vakr.domain.errors import ValidationError
from vakr.domain.quality import Quality
from vakr.ports.snmp import (
    GetResult,
    SnmpError,
    SnmpErrorKind,
    SnmpSessionError,
    SnmpSettings,
    SnmpStats,
    SnmpTarget,
    ValueKind,
    VarBind,
    format_oid,
    in_subtree,
    parse_oid,
    require_oid,
)

UPTIME = (1, 3, 6, 1, 2, 1, 1, 3, 0)


@pytest.mark.parametrize("text", ["1.3.6.1.2.1.1.3.0", ".1.3.6.1.2.1.1.3.0"])
def test_parse_oid_accepts_dotted_decimal(text: str) -> None:
    """SNMP-019: numeric OIDs parse, with or without a leading dot.

    Args:
        text: An OID as an operator or walk file writes it.

    Returns:
        None

    Raises:
        None
    """
    assert parse_oid(text) == UPTIME
    assert format_oid(parse_oid(text)) == "1.3.6.1.2.1.1.3.0"


@pytest.mark.parametrize(
    "text", ["", "1", "1.3.6.x", "3.1", "1..3", "1.3.01", "1.3 .6", "1." + ".".join(["1"] * 128)]
)
def test_parse_oid_rejects_malformed_text(text: str) -> None:
    """SNMP-019: names, gaps, leading zeros, bad first arcs, and overlong OIDs are refused.

    Args:
        text: A malformed OID.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        parse_oid(text)


def test_require_oid_checks_arc_range_and_types() -> None:
    """SNMP-019: arcs are unsigned 32-bit integers, never bools or strings.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert require_oid((1, 3, 2**32 - 1)) == (1, 3, 2**32 - 1)
    for bad in [(1, 3, 2**32), (1, True), (1, "3"), [1, 3], (1,)]:
        with pytest.raises(ValidationError):
            require_oid(bad)
    assert in_subtree((1, 3, 6, 1), (1, 3))
    assert not in_subtree((1, 3), (1, 3))


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (ValueKind.INTEGER, 2**31),
        (ValueKind.COUNTER32, -1),
        (ValueKind.COUNTER64, 2**64),
        (ValueKind.IP_ADDRESS, b"\x01\x02"),
        (ValueKind.OCTET_STRING, "text"),
        (ValueKind.OBJECT_ID, (9,)),
        (ValueKind.NULL, 0),
        (ValueKind.TIMETICKS, True),
    ],
)
def test_varbind_rejects_values_that_do_not_fit_their_kind(kind: ValueKind, value: object) -> None:
    """A varbind's value must have its SMIv2 type and range.

    Args:
        kind: The value kind.
        value: A value that does not fit.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        VarBind(UPTIME, kind, value)  # ty: ignore[invalid-argument-type]


def test_varbind_accessors_never_coerce_missing_to_zero() -> None:
    """Invariant 1: an exception kind has no value, and accessors return None, not 0.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    missing = VarBind(UPTIME, ValueKind.NO_SUCH_INSTANCE)
    assert not missing.present
    assert missing.integer() is None
    assert missing.text() is None
    text = VarBind(UPTIME, ValueKind.OCTET_STRING, b"Gi0/0/0\x00\x00")
    assert text.text() == "Gi0/0/0"
    assert text.integer() is None
    assert text.object_id() is None
    oid = VarBind(UPTIME, ValueKind.OBJECT_ID, (1, 3, 6))
    assert oid.object_id() == (1, 3, 6)
    assert oid.octets() is None


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"address": "224.0.0.1"}, "address"),
        ({"address": "0.0.0.0"}, "address"),  # noqa: S104 - refusing it is the point
        ({"address": "::"}, "address"),
        ({"address": "255.255.255.255"}, "address"),
        ({"address": "2001:DB8::1"}, "poll_address"),
        ({"address": "core 1"}, "poll_address"),
        ({"port": 0}, "port"),
        ({"port": 65536}, "port"),
        ({"port": True}, "port"),
        ({"credential": " net "}, "credential"),
        ({"source_address": "host.example.net"}, "source_address"),
        ({"source_address": "2001:db8::9"}, "source_address"),
        ({"source_address": "192.0.2.010"}, "source_address"),
        ({"context": "ctx"}, "context"),
        ({"address": "192.0.2.1", "family": AddressFamily.IPV6}, "poll_address"),
    ],
)
def test_snmp_t11_target_validation_rejects_bad_input(kwargs: dict, field: str) -> None:
    """SNMP-T11: malformed targets, ports, families, sources, and contexts are refused.

    Args:
        kwargs: Overrides of a valid target.
        field: The field the error names.

    Returns:
        None

    Raises:
        None
    """
    base = {"address": "192.0.2.1", "credential": "network-v2c"} | kwargs
    with pytest.raises(ValidationError) as caught:
        SnmpTarget(**base)  # ty: ignore[invalid-argument-type]
    assert caught.value.field == field


def test_snmp_t11_target_accepts_normalised_options() -> None:
    """SNMP-T11: port, family, source address, and v3 context reach the target unchanged.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    v6 = SnmpTarget("2001:db8::1", "net", port=1161, source_address="2001:db8::2")
    assert (v6.label, v6.address_family, v6.source_address) == (
        "[2001:db8::1]:1161",
        AddressFamily.IPV6,
        "2001:db8::2",
    )
    name = SnmpTarget("core1.example.net", "net", family=AddressFamily.IPV4)
    assert name.address_family is AddressFamily.IPV4
    assert name.label == "core1.example.net:161"
    v3 = SnmpTarget("192.0.2.1", "net", version=SnmpVersion.V3, context="vlan-10")
    assert v3.context == "vlan-10"
    with pytest.raises(ValidationError):
        SnmpTarget("192.0.2.1", "net", version=SnmpVersion.V3, context="x" * 33)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_s": 0},
        {"timeout_s": 31},
        {"timeout_s": float("nan")},
        {"retries": 6},
        {"max_varbinds": 101},
        {"min_varbinds": 31},
        {"max_repetitions": 0},
        {"max_walk_rows": 0},
        {"abandon_after": 11},
    ],
)
def test_snmp_settings_are_bounded(kwargs: dict) -> None:
    """Every request limit is bounded (CLAUDE.md, E.18, E.23).

    Args:
        kwargs: An out-of-range setting.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        SnmpSettings(**kwargs)


def test_snmp_settings_defaults_match_appendix_e() -> None:
    """E.4 and E.9: 2 s, 1 retry, 30 varbinds halving to 5, 25 repetitions.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    s = SnmpSettings()
    assert (s.timeout_s, s.retries, s.attempts) == (2.0, 1, 2)
    assert (s.max_varbinds, s.min_varbinds, s.max_repetitions) == (30, 5, 25)


def test_snmp_stats_add_subtract_and_flag_effort() -> None:
    """SNMP-017, SNMP-018: retries and size reductions earn their quality flags.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    a = SnmpStats(requests=2, responses=1, timeouts=1, retries=1, elapsed_s=0.5)
    b = SnmpStats(requests=1, responses=1, size_reductions=1, elapsed_s=0.25)
    assert (a + b).requests == 3
    assert (a + b) - b == a
    assert (a + b).quality == Quality.RETRIED | Quality.REQUEST_SIZE_REDUCED
    assert SnmpStats().quality is Quality.NONE


def test_snmp_errors_name_their_side_and_code() -> None:
    """Invariant 2: collector-side failures are marked; agent errors carry the status name.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert SnmpError(SnmpErrorKind.TRANSPORT, "x").collector_side
    assert not SnmpError(SnmpErrorKind.TIMEOUT, "x").collector_side
    assert SnmpError(SnmpErrorKind.AGENT_ERROR, "x", status=99).code == "agent_error.99"
    assert SnmpError(SnmpErrorKind.TOO_BIG, "x", status=1).code == "too_big"
    error = SnmpSessionError(SnmpError(SnmpErrorKind.CREDENTIAL, "missing"))
    assert str(error) == "missing"
    assert error.error.kind is SnmpErrorKind.CREDENTIAL
    result = GetResult((UPTIME,), (VarBind(UPTIME, ValueKind.TIMETICKS, 5),))
    assert result.complete
    assert result.by_oid()[UPTIME].integer() == 5
