"""Policy rule model: scopes, tiers, conditions, and actions (POLICY-001, POLICY-002).

Each rule has a scope, AND-combined match conditions (each a list of
patterns, OR within the list), and actions: include or exclude from
monitoring, settings, and annotations. Precedence follows POLICY-002:
global, then group/tag, then device, then interface override (``Tier``).
Site rules share the group/tag tier because a site is a set of devices just
as a group is. Within a tier a higher ``priority`` wins, then the later rule
in the file, as in ``.gitignore``: write the general rule first and its
exceptions after it.

``rules_parse`` builds these objects from ``policies.toml``.

Dependencies:
    vakr.policy.patterns, vakr.policy.settings: Conditions and actions.

Safety:
    Rules are immutable value objects; evaluation never changes them.

Example:
    >>> ScopeKind.SITE.tier.name, ScopeKind.INTERFACE.tier > ScopeKind.DEVICE.tier
    ('GROUP', True)
"""

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from vakr.policy.facts import MatchField
from vakr.policy.patterns import Pattern
from vakr.policy.settings import AnnotationAction, SettingValue


class Tier(IntEnum):
    """Precedence tier of a rule's scope (POLICY-002); higher overrides lower."""

    GLOBAL = 0
    GROUP = 1
    DEVICE = 2
    INTERFACE = 3


class ScopeKind(StrEnum):
    """What a rule applies to; each kind maps to one tier."""

    GLOBAL = "global"
    GROUP = "group"
    TAG = "tag"
    SITE = "site"
    DEVICE = "device"
    INTERFACE = "interface"

    @property
    def tier(self) -> Tier:
        """Return the precedence tier of this scope.

        Args:
            None

        Returns:
            Tier: Group, tag, and site scopes share ``Tier.GROUP``.

        Raises:
            None
        """
        tiers = {"global": Tier.GLOBAL, "device": Tier.DEVICE, "interface": Tier.INTERFACE}
        return tiers.get(self.value, Tier.GROUP)


class Action(StrEnum):
    """Monitoring action; excluded interfaces stay inventoried (DISC-007)."""

    INCLUDE = "include"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class Condition:
    """One AND-ed condition: the field matches any of the patterns.

    Attributes:
        field: Fact field tested.
        patterns: Alternatives, in the order written.
        selector: Scope selector key as written (``groups``, ``devices``, ...),
            or ``""`` for a ``match`` entry.
    """

    field: MatchField
    patterns: tuple[Pattern, ...]
    selector: str = ""


@dataclass(frozen=True, slots=True)
class Rule:
    """One validated policy rule; immutable.

    Attributes:
        name: Unique rule name shown in explain output.
        position: 1-based position in the file; later wins on a tie.
        scope: What the rule applies to.
        priority: Higher wins within a tier.
        conditions: Scope selectors, then match conditions in field order.
        action: Include or exclude from monitoring, or ``None``.
        settings: Settings to apply, in ``SETTING_LIMITS`` order.
        annotations: Annotations to assign.
        description: Operator note.
    """

    name: str
    position: int
    scope: ScopeKind
    priority: int
    conditions: tuple[Condition, ...]
    action: Action | None
    settings: tuple[tuple[str, SettingValue], ...]
    annotations: AnnotationAction
    description: str = ""

    @property
    def tier(self) -> Tier:
        """Return the rule's precedence tier.

        Args:
            None

        Returns:
            Tier: Tier of the rule's scope.

        Raises:
            None
        """
        return self.scope.tier

    @property
    def precedence(self) -> tuple[int, int, int]:
        """Return the sort key; rules apply in ascending order and the last wins.

        Args:
            None

        Returns:
            tuple[int, int, int]: ``(tier, priority, position)``.

        Raises:
            None
        """
        return (int(self.tier), self.priority, self.position)


@dataclass(frozen=True, slots=True)
class Policy:
    """An ordered, validated set of rules.

    Attributes:
        rules: Rules in file order.
        source: Where the rules came from, for display.
    """

    rules: tuple[Rule, ...] = ()
    source: str = ""
