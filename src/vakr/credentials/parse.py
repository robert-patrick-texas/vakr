r"""Parse ``credentials.toml`` into credential objects (blueprint 11.3, SEC-002).

The file holds ``[credentials.NAME]`` tables, one per SNMP credential, and a
``[secrets]`` table of named strings such as database DSNs. Every problem
is collected, so one ``vakr credentials check`` shows them all, and is
reported with file, line, and key, never with a value.

Syntax errors are the dangerous case. ``tomllib.TOMLDecodeError`` keeps the
whole document in ``doc`` and some messages quote file characters, so the
error is never re-raised or chained: only its line number survives, plus a
fixed phrase chosen here. The document text stays inside a ``Secret`` and the
parsed dictionary is never bound in a frame that raises, so even a traceback
that captures local variables shows nothing.

Dependencies:
    tomllib: TOML parsing.
    vakr.credentials.model, vakr.credentials.secret: The values built.
    vakr.credentials.keylines: Line numbers for keys.
    vakr.credentials.entry: Validation of one ``[credentials.NAME]`` table.

Safety:
    Values are wrapped in ``Secret`` as soon as they are type-checked.

Example:
    >>> from pathlib import Path
    >>> text = '[credentials.core]\nversion = "2c"\ncommunity = "placeholder"\n'
    >>> parse_credentials(Secret(text), Path("c.toml")).credentials["core"].version.value
    '2c'
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vakr.credentials.entry import SNMP_TABLE, EntryValidator
from vakr.credentials.errors import CredentialFileError, CredentialValueError, Issue, display_key
from vakr.credentials.keylines import KeyLines
from vakr.credentials.model import Credential
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import NAME_RULE, check_name

NAMED_TABLE = "secrets"
# tomllib messages that are fixed text. Others interpolate file content (an
# illegal character, a key) and are replaced by the phrases below.
_FIXED_MESSAGES = frozenset(
    {
        "Invalid statement",
        "Expected newline or end of document after a statement",
        "Cannot overwrite a value",
        "Expected ']' at the end of a table declaration",
        "Expected ']]' at the end of an array declaration",
        "Expected '=' after a key in a key/value pair",
        "Invalid initial character for a key part",
        "Unclosed array",
        "Unclosed inline table",
        "Unescaped '\\' in a string",
        "Invalid hex value",
        "Escaped character is not a Unicode scalar value",
        "Unterminated string",
        "Invalid date or datetime",
        "Invalid value",
    }
)
_PREFIX_PHRASES = (
    ("Found invalid character", "invalid character"),
    ("Illegal character", "illegal character"),
    ("Cannot declare", "a table or key is declared twice"),
    ("Duplicate inline table key", "a key is declared twice in an inline table"),
    ("Cannot mutate immutable namespace", "conflicting table definitions"),
    ("Cannot redefine namespace", "conflicting table definitions"),
)


@dataclass(frozen=True, slots=True)
class CredentialSet:
    """Everything one valid credentials file defines.

    Attributes:
        credentials: SNMP credentials by name.
        secrets: Named secrets from ``[secrets]`` by name.
    """

    credentials: Mapping[str, Credential]
    secrets: Mapping[str, Secret]

    def all_secrets(self) -> tuple[Secret, ...]:
        """Return every secret value in the file, for redaction.

        Args:
            None

        Returns:
            tuple[Secret, ...]: SNMP secrets followed by named secrets.

        Raises:
            None
        """
        snmp = (s for c in self.credentials.values() for s in c.secrets())
        return (*snmp, *self.secrets.values())


def syntax_phrase(message: str) -> str:
    """Turn a ``tomllib`` message into a phrase that cannot contain file text.

    Args:
        message: ``TOMLDecodeError.msg``.

    Returns:
        str: ``TOML syntax error`` plus the fixed message or a fixed summary.

    Raises:
        None
    """
    if message in _FIXED_MESSAGES:
        return f"TOML syntax error: {message[0].lower()}{message[1:]}"
    for prefix, phrase in _PREFIX_PHRASES:
        if message.startswith(prefix):
            return f"TOML syntax error: {phrase}"
    return "TOML syntax error"


def parse_credentials(content: Secret, path: Path) -> CredentialSet:
    """Parse and validate the whole file.

    Args:
        content: The file text.
        path: The file, for messages.

    Returns:
        CredentialSet: The credentials and named secrets.

    Raises:
        CredentialFileError: With every problem found.
    """
    issues: list[Issue] = []
    result = _build(_load_toml(content, path), KeyLines(content.reveal()), issues)
    if issues:
        raise CredentialFileError(path, issues)
    return result


def _load_toml(content: Secret, path: Path) -> dict[str, Any]:
    """Parse TOML, keeping only the line number of a syntax error.

    Args:
        content: The file text.
        path: The file, for messages.

    Returns:
        dict[str, Any]: The parsed document.

    Raises:
        CredentialFileError: On a syntax error, deliberately not chained.
    """
    try:
        return tomllib.loads(content.reveal())
    except tomllib.TOMLDecodeError as exc:
        issue = Issue(exc.lineno, None, syntax_phrase(exc.msg))
    except RecursionError:
        issue = Issue(None, None, "TOML syntax error: keys or values are nested too deeply")
    raise CredentialFileError(path, [issue])


def _build(document: dict[str, Any], lines: KeyLines, issues: list[Issue]) -> CredentialSet:
    """Validate the top level and build both tables.

    Args:
        document: The parsed document.
        lines: Key line numbers.
        issues: Collects problems.

    Returns:
        CredentialSet: Whatever was valid; meaningful only without issues.

    Raises:
        None
    """
    for key in sorted(document.keys() - {SNMP_TABLE, NAMED_TABLE}):
        reason = "unknown top-level key; expected [credentials.NAME] tables and [secrets]"
        issues.append(Issue(lines.find((key,)), display_key(key), reason))
    credentials: dict[str, Credential] = {}
    for name, table in _tables(document, SNMP_TABLE, lines, issues).items():
        entry = EntryValidator(name, table, lines)
        credential = entry.build()
        issues.extend(entry.issues)
        if credential is not None:
            credentials[name] = credential
    named: dict[str, Secret] = {}
    for name, value in _tables(document, NAMED_TABLE, lines, issues).items():
        key, line = f"{NAMED_TABLE}.{display_key(name)}", lines.find((NAMED_TABLE, name))
        reason = _named_reason(name, value)
        if reason is None:
            named[name] = Secret(value)
        else:
            issues.append(Issue(line, key, reason))
    return CredentialSet(credentials, named)


def _tables(
    document: dict[str, Any], table: str, lines: KeyLines, issues: list[Issue]
) -> dict[str, Any]:
    """Return one top-level table, or an empty one with an issue if it is not a table.

    Args:
        document: The parsed document.
        table: ``credentials`` or ``secrets``.
        lines: Key line numbers.
        issues: Collects problems.

    Returns:
        dict[str, Any]: The table's entries.

    Raises:
        None
    """
    value = document.get(table, {})
    if isinstance(value, dict):
        return value
    issues.append(Issue(lines.find((table,)), table, "must be a table"))
    return {}


def _named_reason(name: str, value: object) -> str | None:
    """Explain why a ``[secrets]`` entry is invalid.

    Args:
        name: Entry name.
        value: Entry value.

    Returns:
        str | None: The broken rule, or ``None`` when the entry is valid.

    Raises:
        None
    """
    try:
        check_name(name)
    except CredentialValueError:
        return f"name {NAME_RULE}"
    if not isinstance(value, str):
        return "must be a string"
    return None if value else "must not be empty"
