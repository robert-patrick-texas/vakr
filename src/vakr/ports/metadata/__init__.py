"""Metadata persistence ports: inventory repositories and their unit of work (5.2).

Import everything an adapter or a use case needs from here.

Dependencies:
    vakr.ports.metadata.filters, repository, reviews, users, sessions, auth,
    unit_of_work: The definitions.

Safety:
    See ``vakr.ports``: no driver types cross these interfaces.

Example:
    >>> from vakr.ports.metadata import MetadataUnitOfWork, DeviceFilter
    >>> DeviceFilter().include_retired
    False
"""

from vakr.ports.metadata.filters import AuditFilter, DeviceFilter, InterfaceFilter, NoFilter
from vakr.ports.metadata.repository import (
    AuditRepository,
    CollectorRepository,
    DeviceRepository,
    EntityRepository,
    GroupRepository,
    InterfaceRepository,
    NamedRepository,
    PolicyRepository,
    SiteRepository,
    TagRepository,
)
from vakr.ports.metadata.reviews import IdentityReviewRepository, ReviewFilter
from vakr.ports.metadata.sessions import KioskTokenRepository, SessionRepository
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWork, MetadataUnitOfWorkFactory
from vakr.ports.metadata.users import LoginFailureRepository, UserFilter, UserRepository

__all__ = [
    "AuditFilter",
    "AuditRepository",
    "CollectorRepository",
    "DeviceFilter",
    "DeviceRepository",
    "EntityRepository",
    "GroupRepository",
    "IdentityReviewRepository",
    "InterfaceFilter",
    "InterfaceRepository",
    "KioskTokenRepository",
    "LoginFailureRepository",
    "MetadataUnitOfWork",
    "MetadataUnitOfWorkFactory",
    "NamedRepository",
    "NoFilter",
    "PolicyRepository",
    "ReviewFilter",
    "SessionRepository",
    "SiteRepository",
    "TagRepository",
    "UserFilter",
    "UserRepository",
]
