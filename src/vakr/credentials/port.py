"""The credential resolver port: how collectors and services obtain secrets (SEC-002).

Collectors hold only ``CredentialReference`` values from device records and
ask a resolver for the credential immediately before opening an SNMP
session; services ask it for named secrets such as database DSNs. The file
provider implements this port today, and a Vault or other secret-store
provider will implement the same two methods later, so nothing that resolves
credentials has to change (blueprint 15.2).

Dependencies:
    typing: ``Protocol`` for structural typing.
    vakr.credentials.model, vakr.credentials.vocabulary: Port value types.

Safety:
    Implementations must never return, log, or raise secret values except
    wrapped in ``Secret``; presentation layers never call a resolver (3.3).

Example:
    >>> from vakr.credentials.provider import FileCredentialProvider
    >>> isinstance(FileCredentialProvider(), CredentialResolver)
    True
"""

from typing import Protocol, runtime_checkable

from vakr.credentials.model import Credential
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import CredentialReference


@runtime_checkable
class CredentialResolver(Protocol):
    """Resolve credential references and named secrets."""

    def resolve(self, ref: CredentialReference) -> Credential:
        """Return the current credential for a reference.

        Args:
            ref: The reference from a device record.

        Returns:
            Credential: The credential, read as late as practical.

        Raises:
            CredentialError: If the provider cannot supply it; the message
                never contains a secret.
        """
        ...

    def secret(self, name: str) -> Secret:
        """Return a named secret, such as a database DSN.

        Args:
            name: Secret name, such as ``metadata-dsn``.

        Returns:
            Secret: The wrapped value.

        Raises:
            CredentialError: If the provider cannot supply it.
        """
        ...
