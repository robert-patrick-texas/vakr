"""Test helpers shared by the SNMP simulator tests: credentials and communities.

Communities are generated with ``secrets.token_hex`` for every test run, so
nothing resembling a community is committed, and finding one in output can
only mean a leak.

Dependencies:
    secrets: Unique fake communities.
    vakr.credentials: The resolver protocol and credential types.

Example:
    >>> resolver = StaticResolver({"sim": fake_community()})
    >>> resolver.resolve(CredentialReference("sim")).name
    'sim'
"""

import secrets
from collections.abc import Mapping
from pathlib import Path

from vakr.credentials import (
    Credential,
    CredentialNotFoundError,
    CredentialReference,
    Secret,
    SnmpV2cCredential,
)


def fake_community(prefix: str = "simc") -> str:
    """Return a fresh, unique fake community.

    Args:
        prefix: Readable prefix; it makes leaks easy to recognise.

    Returns:
        str: For example ``simc-3f9a...``.

    Raises:
        None
    """
    return f"{prefix}-{secrets.token_hex(10)}"


class StaticResolver:
    """A ``CredentialResolver`` over an in-memory map of v2c communities.

    Attributes:
        calls: How many times ``resolve`` ran.
    """

    def __init__(self, communities: Mapping[str, str]) -> None:
        """Wrap each community in a credential.

        Args:
            communities: Credential name to community.

        Returns:
            None

        Raises:
            None
        """
        self._credentials = {
            name: SnmpV2cCredential(name, Secret(value)) for name, value in communities.items()
        }
        self.calls = 0

    def __repr__(self) -> str:
        """Name the credentials only.

        Args:
            None

        Returns:
            str: The credential names.

        Raises:
            None
        """
        return f"StaticResolver({sorted(self._credentials)})"

    def resolve(self, ref: CredentialReference) -> Credential:
        """Return the credential for a reference.

        Args:
            ref: The reference.

        Returns:
            Credential: The v2c credential.

        Raises:
            CredentialNotFoundError: If the name is unknown.
        """
        self.calls += 1
        credential = self._credentials.get(ref.name)
        if credential is None:
            raise CredentialNotFoundError(ref.name, _NOWHERE)
        return credential

    def secret(self, name: str) -> Secret:
        """Refuse named secrets, which these tests never need.

        Args:
            name: The secret name.

        Returns:
            Secret: Never returns.

        Raises:
            CredentialNotFoundError: Always.
        """
        raise CredentialNotFoundError(name, _NOWHERE)


_NOWHERE = Path("in-memory-test-credentials")
