"""Settings and annotations a policy rule may set, within documented limits (POLICY-007).

A rule can poll a WAN circuit more often, discover a lab less often, change
the reachability strategy, or tune SNMP timeouts for a slow satellite link.
Each setting has a hard range so a typo such as ``polling_interval = "1s"``
fails validation instead of flooding a device (section 11.4, "intervals
below safe configured minimums"). Settings a rule does not set keep the
configured defaults (``[polling]``, Appendix E.9).

The ranges are listed in ``SETTING_LIMITS`` and in
``docs/operations/policies.md``; Appendix E does not yet define them, so they
are proposed for it.

Dependencies:
    vakr.domain.enums: Interest class and reachability strategy.
    vakr.lifecycle.configmerge: The shared duration parser (``"60s"``).

Safety:
    Only the listed keys are accepted; unknown keys fail with their path.

Example:
    >>> issues = []
    >>> parse_settings({"polling_interval": "10s"}, "s", issues)
    ()
    >>> str(issues[0])
    's.polling_interval: must be between 30s and 1h'
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from vakr.domain.enums import InterestClass, ReachabilityStrategy
from vakr.lifecycle.configmerge import parse_duration
from vakr.policy.errors import PolicyIssue
from vakr.policy.reader import TableReader

type SettingValue = float | int | bool | ReachabilityStrategy

DAY = 86400


@dataclass(frozen=True, slots=True)
class Limit:
    """Type and inclusive range of one policy setting.

    Attributes:
        kind: ``duration`` (seconds), ``int``, ``bool``, or ``strategy``.
        low: Smallest allowed value, in seconds for durations.
        high: Largest allowed value, in seconds for durations.
        summary: What the setting controls, for documentation.
    """

    kind: str
    low: float = 0
    high: float = 0
    summary: str = ""


SETTING_LIMITS: Mapping[str, Limit] = {
    "polling_interval": Limit("duration", 30, 3600, "interface poll interval"),
    "discovery_interval": Limit("duration", 30, 30 * DAY, "rediscovery interval (DISC-002)"),
    "reachability_strategy": Limit("strategy", summary="ICMP gating of SNMP (ICMP-006)"),
    "snmp_timeout": Limit("duration", 1, 30, "SNMP request timeout (SNMP-011)"),
    "snmp_retries": Limit("int", 0, 5, "SNMP retries (SNMP-011)"),
    "adaptive_polling": Limit("bool", summary="adaptive interval changes (ADR-012)"),
    "stale_after": Limit("duration", DAY, 365 * DAY, "time inactive before STALE (LIFE-003)"),
}
"""Every setting a rule may change, in display order."""


@dataclass(frozen=True, slots=True)
class AnnotationAction:
    """Annotations a rule assigns; ``None`` leaves the value to other sources.

    Attributes:
        interest: Interest class (META-011).
        sticky: Sticky flag (LIFE-007).
        wan_circuit: WAN-circuit flag.
    """

    interest: InterestClass | None = None
    sticky: bool | None = None
    wan_circuit: bool | None = None

    def items(self) -> tuple[tuple[str, InterestClass | bool], ...]:
        """Return the annotations this action sets, in a fixed order.

        Args:
            None

        Returns:
            tuple[tuple[str, InterestClass | bool], ...]: ``(name, value)`` pairs.

        Raises:
            None
        """
        pairs = (("interest", self.interest), ("sticky", self.sticky))
        pairs += (("wan_circuit", self.wan_circuit),)
        return tuple((name, value) for name, value in pairs if value is not None)


def format_seconds(seconds: float) -> str:
    """Render seconds compactly, such as ``90s``, ``5m``, ``1h``, or ``10d``.

    Args:
        seconds: Duration in seconds.

    Returns:
        str: The largest whole unit that represents the value exactly.

    Raises:
        None
    """
    for unit, size in (("d", DAY), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{seconds:g}s"


def format_setting(name: str, value: SettingValue | StrEnum) -> str:
    """Render a setting, annotation, or action value the way an operator writes it.

    Args:
        name: Setting, annotation, or ``monitor``.
        value: Its value.

    Returns:
        str: ``"60s"``, ``"icmp_gated"``, ``"include"``, ``"true"``, ``"3"``, ...

    Raises:
        None
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, StrEnum):
        return value.value
    limit = SETTING_LIMITS.get(name)
    if limit is not None and limit.kind == "duration":
        return format_seconds(value)
    return str(value)


def _setting(reader: TableReader, name: str, limit: Limit) -> SettingValue | None:
    """Read one setting and check it against its limit.

    Args:
        reader: Reader over the ``settings`` table.
        name: Setting name.
        limit: Its type and range.

    Returns:
        SettingValue | None: The value, or ``None`` when absent or invalid.

    Raises:
        None
    """
    if limit.kind == "bool":
        return reader.boolean(name)
    if limit.kind == "int":
        return reader.integer(name, int(limit.low), int(limit.high))
    if limit.kind == "strategy":
        choice = reader.choice(name, [s.value for s in ReachabilityStrategy])
        return None if choice is None else ReachabilityStrategy(choice)
    return _duration(reader, name, limit)


def _duration(reader: TableReader, name: str, limit: Limit) -> float | None:
    """Read a duration such as ``"60s"`` and check its range.

    Args:
        reader: Reader over the ``settings`` table.
        name: Setting name.
        limit: Its range in seconds.

    Returns:
        float | None: Seconds, or ``None`` when absent or invalid.

    Raises:
        None
    """
    text = reader.text(name)
    if text is None:
        return None
    try:
        seconds = parse_duration(text)
    except ValueError as exc:
        reader.problem(name, str(exc))
        return None
    if not limit.low <= seconds <= limit.high:
        span = f"{format_seconds(limit.low)} and {format_seconds(limit.high)}"
        reader.problem(name, f"must be between {span}")
        return None
    return seconds


def parse_settings(
    value: object, path: str, issues: list[PolicyIssue]
) -> tuple[tuple[str, SettingValue], ...]:
    """Parse a rule's ``settings`` table.

    Args:
        value: The table as loaded.
        path: Dotted location of the table.
        issues: List that problems are appended to.

    Returns:
        tuple[tuple[str, SettingValue], ...]: Valid settings in
        ``SETTING_LIMITS`` order.

    Raises:
        None
    """
    reader = TableReader(value, path, issues, SETTING_LIMITS)
    parsed = ((name, _setting(reader, name, limit)) for name, limit in SETTING_LIMITS.items())
    return tuple((name, v) for name, v in parsed if v is not None)


def parse_annotations(value: object, path: str, issues: list[PolicyIssue]) -> AnnotationAction:
    """Parse a rule's ``annotations`` table; ``high_priority`` aliases interest (META-011).

    Args:
        value: The table as loaded.
        path: Dotted location of the table.
        issues: List that problems are appended to.

    Returns:
        AnnotationAction: The annotations to assign.

    Raises:
        None
    """
    keys = ("interest", "high_priority", "sticky", "wan_circuit")
    reader = TableReader(value, path, issues, keys)
    return AnnotationAction(
        interest=read_interest(reader),
        sticky=reader.boolean("sticky"),
        wan_circuit=reader.boolean("wan_circuit"),
    )


def read_interest(reader: TableReader) -> InterestClass | None:
    """Read ``interest``, accepting ``high_priority`` as its alias (META-011).

    Args:
        reader: Reader over a table that may hold either key.

    Returns:
        InterestClass | None: The interest class, or ``None`` when unset.

    Raises:
        None
    """
    interest = reader.choice("interest", [c.value for c in InterestClass])
    alias = reader.boolean("high_priority")
    implied = None if alias is None else ("primary" if alias else "secondary")
    if interest is not None and implied is not None and interest != implied:
        reader.problem("high_priority", f'contradicts interest = "{interest}"')
    chosen = interest or implied
    return None if chosen is None else InterestClass(chosen)
