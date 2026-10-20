"""Validate one ``[credentials.NAME]`` table and build its credential (SNMP-001, SNMP-002).

Each field is type-checked before use, every problem is recorded as an
``Issue`` with the field's line (or the table header's when the field is
absent), and secret fields are wrapped in ``Secret`` the moment they pass.
The SNMPv3 combination rules themselves live in ``SnmpV3Credential``, so a
credential built in code obeys the same rules as one read from the file.

Dependencies:
    vakr.credentials.model, vakr.credentials.vocabulary: Types and choices.
    vakr.credentials.keylines: Line numbers.

Safety:
    Issues name fields and rules only. A rejected choice lists the allowed
    values, never the value given.

Example:
    >>> v = EntryValidator("core", {"version": "2c", "community": "placeholder"}, KeyLines(""))
    >>> v.build(), v.issues
    (SnmpV2cCredential(name='core', community=Secret('***')), [])
"""

from enum import StrEnum

from vakr.credentials.errors import CredentialValueError, Issue, display_key
from vakr.credentials.keylines import KeyLines
from vakr.credentials.model import Credential, SnmpV2cCredential, SnmpV3Credential
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import AuthProtocol, PrivProtocol, SecurityLevel, check_name

SNMP_TABLE = "credentials"
_V2C_FIELDS = frozenset({"version", "community"})
_V3_FIELDS = frozenset(
    {
        "version",
        "username",
        "security_level",
        "auth_protocol",
        "auth_secret",
        "privacy_protocol",
        "privacy_secret",
    }
)


class EntryValidator:
    """One ``[credentials.NAME]`` table being validated.

    Attributes:
        issues: Problems found in this table.
    """

    def __init__(self, name: str, table: object, lines: KeyLines) -> None:
        """Prepare to validate one table.

        Args:
            name: Credential name (the table's key).
            table: The table's parsed value.
            lines: Key line numbers.

        Returns:
            None

        Raises:
            None
        """
        self.name, self.table, self.lines = name, table, lines
        self.issues: list[Issue] = []
        self.header = lines.find((SNMP_TABLE, name))

    def issue(self, key: str | None, reason: str) -> None:
        """Record a problem with one field, or with the table when ``key`` is ``None``.

        Args:
            key: Field name, or ``None``.
            reason: The broken rule.

        Returns:
            None

        Raises:
            None
        """
        dotted = f"{SNMP_TABLE}.{display_key(self.name)}"
        line = self.header
        if key is not None:
            dotted, line = f"{dotted}.{display_key(key)}", self._line(key)
        self.issues.append(Issue(line, dotted, reason))

    def _line(self, key: str) -> int | None:
        """Return the line of a field, falling back to the table header.

        Args:
            key: Field name.

        Returns:
            int | None: Line number, if known.

        Raises:
            None
        """
        return self.lines.find((SNMP_TABLE, self.name, key)) or self.header

    def build(self) -> Credential | None:
        """Validate the table and build its credential.

        Args:
            None

        Returns:
            Credential | None: The credential, or ``None`` when invalid.

        Raises:
            None
        """
        if not isinstance(self.table, dict):
            self.issue(None, "must be a table")
            return None
        version = self.table.get("version")
        if version not in ("2c", "3"):
            reason = "missing required field" if version is None else 'must be "2c" or "3"'
            self.issue("version", reason)
            return None
        allowed = _V2C_FIELDS if version == "2c" else _V3_FIELDS
        for key in sorted(self.table.keys() - allowed):
            self.issue(key, f"unknown field for SNMPv{version}")
        try:
            check_name(self.name)
            credential = self._v2c() if version == "2c" else self._v3()
        except CredentialValueError as exc:
            if exc.field == "name":
                self.issue(None, f"name {exc.reason}")
            else:
                self.issue(exc.field, exc.reason)
            return None
        return None if self.issues else credential

    def _v2c(self) -> Credential | None:
        """Build an SNMPv2c credential.

        Args:
            None

        Returns:
            Credential | None: The credential, or ``None`` if a field is invalid.

        Raises:
            CredentialValueError: If the credential itself rejects a field.
        """
        community = self.secret("community", required=True)
        return None if community is None else SnmpV2cCredential(self.name, community)

    def _v3(self) -> Credential | None:
        """Build an SNMPv3 credential; ``SnmpV3Credential`` checks the combination.

        Args:
            None

        Returns:
            Credential | None: The credential, or ``None`` if a field is invalid.

        Raises:
            CredentialValueError: If the combination is incoherent.
        """
        start = len(self.issues)
        username = self.text("username", required=True)
        level = self.choice("security_level", SecurityLevel, required=True)
        auth = self.choice("auth_protocol", AuthProtocol, required=False)
        privacy = self.choice("privacy_protocol", PrivProtocol, required=False)
        auth_secret = self.secret("auth_secret", required=False)
        privacy_secret = self.secret("privacy_secret", required=False)
        if len(self.issues) > start or username is None or level is None:
            return None
        return SnmpV3Credential(
            self.name, username, level, auth, auth_secret, privacy, privacy_secret
        )

    def text(self, key: str, *, required: bool) -> str | None:
        """Return a non-empty string field, recording a problem otherwise.

        Args:
            key: Field name.
            required: Whether absence is a problem.

        Returns:
            str | None: The value, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.table.get(key) if isinstance(self.table, dict) else None
        if value is None:
            if required:
                self.issue(key, "missing required field")
            return None
        if not isinstance(value, str):
            self.issue(key, "must be a string")
            return None
        if not value:
            self.issue(key, "must not be empty")
            return None
        return value

    def secret(self, key: str, *, required: bool) -> Secret | None:
        """Return a secret field wrapped in ``Secret``.

        Args:
            key: Field name.
            required: Whether absence is a problem.

        Returns:
            Secret | None: The wrapped value, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.text(key, required=required)
        return None if value is None else Secret(value)

    def choice[E: StrEnum](self, key: str, kind: type[E], *, required: bool) -> E | None:
        """Return an enumerated field.

        Args:
            key: Field name.
            kind: The enum whose values are allowed.
            required: Whether absence is a problem.

        Returns:
            E | None: The member, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.text(key, required=required)
        if value is None:
            return None
        member = {m.value: m for m in kind}.get(value)
        if member is None:
            self.issue(key, f"must be one of: {', '.join(m.value for m in kind)}")
        return member
