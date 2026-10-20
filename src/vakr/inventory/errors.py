"""Inventory problems, reported where the operator can find them (11.4, CFG-004).

Every problem names the file and the place in it, for example
``inventory.toml: devices[3].address: 0.0.0.0 is an unspecified address``.
One validation run reports every problem, never just the first, so an
operator fixes a file in one pass.

Dependencies:
    vakr.policy.errors: The shared ``path: reason`` issue record.

Safety:
    Messages name keys, values the operator wrote, and credential *names*;
    never a secret.

Example:
    >>> str(InventoryError("inventory.toml", [Issue("devices[0].name", "required")]))
    'inventory.toml: devices[0].name: required'
"""

from collections.abc import Sequence

from vakr.policy.errors import PolicyIssue as Issue

__all__ = ["InventoryError", "Issue", "render_issues"]


def render_issues(source: str, issues: Sequence[Issue]) -> list[str]:
    """Render problems as ``source: path: reason`` lines.

    Args:
        source: The file name shown first.
        issues: The problems.

    Returns:
        list[str]: One line per problem, in order.

    Raises:
        None
    """
    return [f"{source}: {issue}" if issue.path else f"{source}: {issue.reason}" for issue in issues]


class InventoryError(ValueError):
    """The inventory file, or its relation to stored records, is invalid.

    Attributes:
        source: The file name.
        issues: Every problem, in file order.
    """

    def __init__(self, source: str, issues: Sequence[Issue]) -> None:
        """Record the problems.

        Args:
            source: The file name.
            issues: The problems; at least one.

        Returns:
            None

        Raises:
            None
        """
        self.source = source
        self.issues = tuple(issues)
        super().__init__("\n".join(render_issues(source, self.issues)))

    @property
    def lines(self) -> list[str]:
        """Return the problems as ``source: path: reason`` lines.

        Args:
            None

        Returns:
            list[str]: One line per problem.

        Raises:
            None
        """
        return render_issues(self.source, self.issues)
