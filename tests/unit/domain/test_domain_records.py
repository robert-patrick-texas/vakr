"""Inventory and control record invariants (META-001..013, IFID-005, SEC-018, API-002).

Dependencies:
    pytest; vakr.domain; tests.contract.contract_samples for builders.

Example:
    $ uv run pytest tests/unit/domain/test_domain_records.py
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from tests.contract import contract_samples as s
from vakr.domain.addressing import require_interface_addresses, require_mac, require_poll_address
from vakr.domain.checks import require_float, require_name, require_unchanged
from vakr.domain.enums import AddressFamily, IdentityDecisionSource, ManagedByKind
from vakr.domain.errors import (
    ConflictError,
    DomainError,
    DuplicateError,
    NotFoundError,
    OwnershipError,
    ValidationError,
)
from vakr.domain.ids import DeviceId, TagId, new_id, parse_id, parse_uuid
from vakr.domain.interfaces import InterfaceAnnotation
from vakr.domain.inventory import API_MANAGED, ManagedBy


def test_api002_ids_are_uuid7_and_parse_strictly() -> None:
    """META-001, API-002: new IDs are time-ordered UUIDv7; parsing accepts only canonical text.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    first, second = new_id(DeviceId), new_id(DeviceId)
    assert first.version == 7
    assert first < second
    assert parse_id(DeviceId, str(first).upper()) == first
    for bad in ("", "123", "{" + str(first) + "}", first.hex, 42, None, str(UUID(int=0))):
        with pytest.raises(ValidationError):
            parse_id(DeviceId, bad)
    with pytest.raises(ValidationError, match="tag_id"):
        parse_uuid("x" * 500, "tag_id")


def test_errors_carry_operator_detail() -> None:
    """API-007: every error is a ``DomainError`` with the fields the API reports.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    errors: list[DomainError] = [
        ValidationError("bad", field="name"),
        NotFoundError("device", "d1"),
        ConflictError("device", "d1", expected_version=1, actual_version=2),
        DuplicateError("device", "name", "core1"),
        OwnershipError("device", "d1", owner="file:/a.toml", writer="api:api"),
    ]
    assert isinstance(errors[0], ValueError)
    assert "reload and retry" in str(errors[2])
    assert "file:/a.toml" in str(errors[4])
    assert "core1 already exists" in str(errors[3])
    assert "d1 does not exist" in str(errors[1])


def test_meta008_ownership_rules() -> None:
    """META-008, 11.2: files own their records; the API owns the rest.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    owner = ManagedBy.file("/etc/vakr/a.toml")
    assert owner.permits(owner)
    assert not owner.permits(ManagedBy.file("/etc/vakr/b.toml"))
    assert not owner.permits(API_MANAGED)
    assert API_MANAGED.permits(ManagedBy(ManagedByKind.API, "import"))
    assert str(owner) == "file:/etc/vakr/a.toml"
    with pytest.raises(ValidationError):
        ManagedBy(ManagedByKind.FILE, " padded")
    with pytest.raises(ValidationError):
        ManagedBy(cast(Any, "file"), "x")


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "two\nlines"},
        {"poll_address": "010.0.0.1"},
        {"poll_address": "2001:DB8::1"},
        {"poll_address": "10.0.0"},
        {"poll_address": "-bad-.example"},
        {"poll_address": "a" * 254},
        {"poll_address": ""},
        {"poll_address": "192.0.2.1", "address_family": AddressFamily.IPV6},
        {"poll_interval_s": 0},
        {"poll_interval_s": True},
        {"credential_ref": " "},
        {"tags": {new_id(TagId)}},
        {"created_at": datetime(2026, 1, 1)},
        {"updated_at": s.T0 - s.MINUTE},
        {"version": 0},
        {"managed_by": "api"},
        {"comment": None},
    ],
)
def test_device_invariants(overrides: dict[str, Any]) -> None:
    """META-001/003/005, 9.11: invalid devices cannot be built.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.device(**overrides)


def test_valid_addresses_are_accepted() -> None:
    """9.11: canonical IPs and host names pass; the family must match.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    require_poll_address("2001:db8::1", AddressFamily.IPV6)
    require_poll_address("core1.example.net.", None)
    require_interface_addresses(("10.0.0.1/30",), "addresses")
    require_mac(None, "mac")
    for bad in (
        ["10.0.0.1/30"],
        ("10.0.0.1/33",),
        ("10.0.0.01/30",),
        ("10.0.0.1/30", "10.0.0.1/30"),
    ):
        with pytest.raises(ValidationError):
            require_interface_addresses(bad, "addresses")
    for bad_mac in ("00:1A:2B:3C:4D:5E", "001a2b3c4d5e", 5):
        with pytest.raises(ValidationError):
            require_mac(bad_mac, "mac")


@pytest.mark.parametrize(
    "overrides",
    [
        {"if_index": 0},
        {"if_index": 2**31},
        {"if_alias": None},
        {"lifecycle": "active"},
        {"mac": "zz"},
        {"if_speed": -1},
        {"addresses": ("10.0.0.1",) * 2},
        {"description_tags": [new_id(TagId)]},
        {"annotation": {}},
    ],
)
def test_interface_invariants(overrides: dict[str, Any]) -> None:
    """META-002, invariant 6: invalid interfaces cannot be built.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        s.interface(new_id(DeviceId), **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"interest": "primary"},
        {"sticky": 1},
        {"provider": ""},
        {"contracted_bps": 0},
        {"display_rank": -1},
        {"site_override": "dal1"},
    ],
)
def test_annotation_invariants(overrides: dict[str, Any]) -> None:
    """META-007, META-011: invalid annotations cannot be built.

    Args:
        overrides: Field values that break a rule.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValidationError):
        InterfaceAnnotation(**overrides)


def test_catalog_and_control_invariants() -> None:
    """META-005/006, POLICY, IFID-005, SEC-018: catalog and control records validate.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    iface = s.interface(new_id(DeviceId))
    bad: list[Any] = [
        lambda: s.site(code=""),
        lambda: s.site(timezone=" UTC"),
        lambda: s.tag(description=None),
        lambda: s.group(name=" core"),
        lambda: s.collector(enabled="yes"),
        lambda: s.policy(rules={"a": (1, 2)}),
        lambda: s.policy(rules=[1]),
        lambda: s.policy(order=cast(Any, 1.5)),
        lambda: s.history(iface, s.T0, score=float("inf")),
        lambda: s.history(iface, s.T0, runner_up_score=1),
        lambda: s.history(iface, s.T0, evidence=["x"]),
        lambda: s.history(iface, s.T0, evidence=("",)),
        lambda: s.history(iface, s.T0, source=IdentityDecisionSource.ADMIN),
        lambda: s.history(iface, s.T0, previous_if_index=0),
        lambda: s.audit(datetime(2026, 1, 1)),
        lambda: s.audit(s.T0, action=""),
        lambda: s.audit(s.T0, object_id="d1"),
        lambda: s.audit(s.T0, request_id=""),
    ]
    for build in bad:
        with pytest.raises(ValidationError):
            build()
    admin = s.history(iface, s.T0, source=IdentityDecisionSource.ADMIN, actor="alice", score=None)
    assert admin.actor == "alice"
    assert s.collector().enabled


def test_check_helpers() -> None:
    """Invariant 1: helper checks refuse booleans, NaN, and negative zero.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for bad in (True, float("nan"), -0.0, -1.0, 1):
        with pytest.raises(ValidationError):
            require_float(bad, "rate")
    require_name(None, "name", optional=True)
    dev = s.device()
    require_unchanged(dev, replace(dev, comment="x"), ("id", "name"))
    with pytest.raises(ValidationError, match="name"):
        require_unchanged(dev, replace(dev, name="other"), ("id", "name"))
    assert datetime.now(UTC) > s.T0
