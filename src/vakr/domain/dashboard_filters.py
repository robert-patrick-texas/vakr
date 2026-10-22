"""Dashboard widget filters: which interfaces or devices a widget covers (UI-004, DASH-006).

A selection or ranking widget narrows the inventory by site, tag, group,
provider, WAN role, or interest class. Filters refer to inventory by UUID,
never by name, so renaming a site never breaks a dashboard; ``None`` (or
``False``) means "do not filter".

Dependencies:
    dataclasses: Frozen records.
    vakr.domain.checks, enums, errors, ids: Shared invariant checks.

Safety:
    Display text and UUIDs only.

Example:
    >>> InterfaceCriteria(wan_only=True) == InterfaceCriteria()
    False
"""

from dataclasses import dataclass

from vakr.domain.checks import require_bool, require_enum, require_name, require_uuid
from vakr.domain.enums import InterestClass
from vakr.domain.errors import ValidationError
from vakr.domain.ids import GroupId, SiteId, TagId

MAX_PROVIDER_LENGTH = 128


@dataclass(frozen=True, slots=True)
class InterfaceCriteria:
    """Which interfaces an interface selection or ranking covers; ``None`` does not filter.

    Attributes:
        site_id: Interfaces at this site (an override, else the device's).
        tag_id: Interfaces with this tag, or on a device with it.
        group_id: Interfaces of devices in this group.
        provider: Circuits of this provider (case-insensitive).
        wan_only: Only WAN circuits.
        interest: Only this interest class (``primary`` is high priority).
    """

    site_id: SiteId | None = None
    tag_id: TagId | None = None
    group_id: GroupId | None = None
    provider: str | None = None
    wan_only: bool = False
    interest: InterestClass | None = None

    def __post_init__(self) -> None:
        """Validate the criteria.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field has the wrong type or the provider is too long.
        """
        for name in ("site_id", "tag_id", "group_id"):
            require_uuid(getattr(self, name), name, optional=True)
        require_name(self.provider, "provider", optional=True)
        if self.provider is not None and len(self.provider) > MAX_PROVIDER_LENGTH:
            raise ValidationError(
                f"provider must be at most {MAX_PROVIDER_LENGTH} characters", field="provider"
            )
        require_bool(self.wan_only, "wan_only")
        require_enum(self.interest, InterestClass, "interest", optional=True)


@dataclass(frozen=True, slots=True)
class DeviceCriteria:
    """Which devices a device selection covers; ``None`` does not filter.

    Attributes:
        site_id: Devices at this site.
        tag_id: Devices with this tag.
        group_id: Devices in this group.
    """

    site_id: SiteId | None = None
    tag_id: TagId | None = None
    group_id: GroupId | None = None

    def __post_init__(self) -> None:
        """Validate the criteria.

        Args:
            None

        Returns:
            None

        Raises:
            ValidationError: If a field is not a UUID.
        """
        for name in ("site_id", "tag_id", "group_id"):
            require_uuid(getattr(self, name), name, optional=True)
