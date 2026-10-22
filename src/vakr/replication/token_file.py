"""Read the partner's replication token from ``/etc/vakr/secrets/replication.token`` (MPOS-006).

The file holds one token, ``vkr_...``, that the partner node issued for this
one. It is checked like the credentials file (SEC-016): owned by root or
``vakr``, readable by the ``vakr`` group at most, writable by nobody else,
opened without following symlinks, and checked on the open descriptor.
Strict mode cannot be turned off for the services.

Dependencies:
    vakr.credentials.protected, policy: The race-free strict read.
    vakr.auth.tokens: The prefix and size rules.

Safety:
    The token is a ``Secret`` from the moment it is read; problems name the
    file, its owner, and its mode, never its content.

Example:
    >>> from pathlib import Path
    >>> token_path(Path("/etc/vakr/secrets"))
    PosixPath('/etc/vakr/secrets/replication.token')
"""

from pathlib import Path

from vakr.auth.tokens import MAX_TOKEN_LENGTH, REPLICATION_PREFIX
from vakr.config.node import TOKEN_FILE
from vakr.credentials.errors import CredentialError
from vakr.credentials.policy import FilePolicy
from vakr.credentials.protected import read_protected
from vakr.credentials.secret import Secret


class TokenFileError(Exception):
    """The token file is missing, unsafe, or does not hold a replication token."""


def token_path(secrets_dir: Path) -> Path:
    """Return where this node keeps its partner's token.

    Args:
        secrets_dir: ``/etc/vakr/secrets`` (``InstallPaths.secrets_dir``).

    Returns:
        Path: The token file.

    Raises:
        None
    """
    return secrets_dir / TOKEN_FILE


def read_token(path: Path, policy: FilePolicy | None = None) -> Secret:
    """Read and check the token file (strict mode, always).

    Args:
        path: The token file.
        policy: Ownership rules; the host's ``vakr`` accounts by default.

    Returns:
        Secret: The token.

    Raises:
        TokenFileError: If the file is missing, unsafe, or not one ``vkr_`` token.
    """
    try:
        found = read_protected(path, policy or FilePolicy.for_host(), strict=True)
    except CredentialError as exc:
        raise TokenFileError(f"{exc}; the partner node issues the token with "
                             "`sudo vakr replication token create`") from None  # fmt: skip
    text = found.content.reveal().strip()
    if not text.startswith(REPLICATION_PREFIX) or len(text) > MAX_TOKEN_LENGTH or "\n" in text:
        raise TokenFileError(f"{path} does not hold one replication token (vkr_...)")
    return Secret(text)
