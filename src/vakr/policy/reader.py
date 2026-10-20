"""Typed reads from TOML/JSON tables that collect problems instead of raising.

Policy, keyword, and fact files arrive as plain dicts from ``tomllib`` or
``json``. Every parser in this package reads them through ``TableReader`` so
unknown keys, wrong types, and out-of-range numbers are reported the same
way, with a dotted path, and so one validation run lists every problem
rather than stopping at the first (section 11.4).

Dependencies:
    vakr.policy.errors: The issue record.

Safety:
    Readers never evaluate or execute values; they only check types.

Example:
    >>> issues = []
    >>> r = TableReader({"priority": "high"}, "rules[0]", issues, {"priority"})
    >>> r.integer("priority"), str(issues[0])
    (None, 'rules[0].priority: expected an integer')
"""

from collections.abc import Collection, Mapping
from typing import Any

from vakr.policy.errors import PolicyIssue

MAX_LIST_LENGTH = 256
"""Longest list accepted for any one field, keeping evaluation bounded."""


class TableReader:
    """Read typed values from one table; problems go to a shared issue list.

    Instances are short-lived and single-threaded: one per table parsed.
    """

    def __init__(
        self,
        table: object,
        path: str,
        issues: list[PolicyIssue],
        allowed: Collection[str],
    ) -> None:
        """Check that ``table`` is a table holding only ``allowed`` keys.

        Args:
            table: Value that should be a mapping.
            path: Dotted path of the table, used as the issue prefix.
            issues: List that problems are appended to.
            allowed: Keys this table may contain.

        Returns:
            None

        Raises:
            None
        """
        self.path = path
        self.issues = issues
        self.data: Mapping[str, Any] = table if isinstance(table, Mapping) else {}
        if not isinstance(table, Mapping):
            self.problem("", "expected a table")
        for key in sorted(set(self.data) - set(allowed), key=str):
            self.problem(str(key), f"unknown key; expected one of: {', '.join(sorted(allowed))}")

    def at(self, key: str) -> str:
        """Return the dotted path of ``key`` inside this table.

        Args:
            key: Key name, or ``""`` for the table itself.

        Returns:
            str: Path such as ``rules[0].priority``.

        Raises:
            None
        """
        if not key:
            return self.path
        return f"{self.path}.{key}" if self.path else key

    def problem(self, key: str, reason: str) -> None:
        """Record a problem with ``key``.

        Args:
            key: Key name, or ``""`` for the table itself.
            reason: What is wrong.

        Returns:
            None

        Raises:
            None
        """
        self.issues.append(PolicyIssue(self.at(key), reason))

    def text(self, key: str, *, required: bool = False) -> str | None:
        """Read a string.

        Args:
            key: Key name.
            required: Record a problem when the key is absent.

        Returns:
            str | None: The string, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.data.get(key)
        if value is None:
            if required:
                self.problem(key, "required")
            return None
        if not isinstance(value, str):
            self.problem(key, "expected a string")
            return None
        return value

    def integer(self, key: str, low: int | None = None, high: int | None = None) -> int | None:
        """Read an integer, optionally within ``[low, high]``; booleans are rejected.

        Args:
            key: Key name.
            low: Smallest allowed value, or ``None``.
            high: Largest allowed value, or ``None``.

        Returns:
            int | None: The integer, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.data.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self.problem(key, "expected an integer")
            return None
        if low is not None and value < low:
            self.problem(key, f"must be at least {low}")
            return None
        if high is not None and value > high:
            self.problem(key, f"must be at most {high}")
            return None
        return value

    def boolean(self, key: str) -> bool | None:
        """Read a boolean.

        Args:
            key: Key name.

        Returns:
            bool | None: The boolean, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.data.get(key)
        if value is None or isinstance(value, bool):
            return value
        self.problem(key, "expected true or false")
        return None

    def strings(self, key: str) -> tuple[str, ...]:
        """Read a bounded list of strings.

        Args:
            key: Key name.

        Returns:
            tuple[str, ...]: The strings; empty when absent or invalid.

        Raises:
            None
        """
        value = self.data.get(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self.problem(key, "expected a list of strings")
            return ()
        if len(value) > MAX_LIST_LENGTH:
            self.problem(key, f"at most {MAX_LIST_LENGTH} entries are allowed")
            return ()
        return tuple(value)

    def choice(self, key: str, choices: Collection[str]) -> str | None:
        """Read a string that must be one of ``choices``.

        Args:
            key: Key name.
            choices: Allowed values.

        Returns:
            str | None: The value, or ``None`` when absent or invalid.

        Raises:
            None
        """
        value = self.text(key)
        if value is not None and value not in choices:
            self.problem(key, f"expected one of: {', '.join(choices)}")
            return None
        return value
