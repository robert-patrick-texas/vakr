"""Poll plans from stored inventory: which ifIndexes to poll and for which identity (SNMP-004/005).

A device's plan lists every interface with a current ``ifIndex``:

- a resolved identity carries its ``interface_id``; ``collect`` is the
  policy decision (POLICY-*, DISC-007: suppressed interfaces stay in
  inventory and in the plan for status, without counters). ``STALE``
  interfaces drop status checks to the discovery cadence (LIFE-004), and
  ``RETIRED`` ones leave the plan;
- a row waiting for identity review is planned with ``interface_id=None``,
  and its candidates are left out entirely, so neither old nor new identity
  receives observations until an administrator decides (IFID-004). The
  collector flags such samples ``BASELINE_IDENTITY_UNRESOLVED``.

Policy is evaluated on stored facts only. Interface admin and oper status
are not stored, so rules that match on them never match here; Stage 2
gates counters on live up/up status anyway (SNMP-006). Counter
capabilities come from the latest discovery seen by this process, and
default to trying 64-bit counters (an unknown capability is tried, never
assumed missing).

Dependencies:
    vakr.collectors.snmp.plan: ``PollPlan`` and ``InterfacePlan``.
    vakr.discovery.facts: Device context and the physical-port rule.
    vakr.policy: The decision engine and its facts.

Safety:
    Read-only.

Example:
    >>> Capabilities().family.value
    'hc64'
"""

from collections.abc import Mapping
from dataclasses import dataclass

from vakr.collectors.snmp.plan import InterfacePlan, PollPlan
from vakr.discovery.facts import DeviceContext, is_physical
from vakr.domain.enums import CounterFamily, InterestClass, InterfaceLifecycle
from vakr.domain.ids import InterfaceId, TagId
from vakr.domain.interfaces import Interface
from vakr.domain.inventory import Device
from vakr.domain.reviews import IdentityReview
from vakr.inventory.catalog import group_names, tag_names
from vakr.inventory.reads import device_interfaces, open_reviews
from vakr.policy.engine import decide
from vakr.policy.facts import Annotations, Circuit, InterfaceFacts
from vakr.policy.keywords import KeywordSet
from vakr.policy.rules import Policy
from vakr.ports.metadata import MetadataUnitOfWork


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Counter capabilities discovery probed for one interface (SNMP-010).

    Attributes:
        hc_octets: 64-bit octet counters exist; ``None`` when unknown.
        hc_packets: 64-bit packet counters exist; ``None`` when unknown.
        discontinuity: ``ifCounterDiscontinuityTime`` exists; ``None`` when unknown.
    """

    hc_octets: bool | None = None
    hc_packets: bool | None = None
    discontinuity: bool | None = None

    @property
    def family(self) -> CounterFamily:
        """Counter family to request: HC unless discovery proved it missing.

        Args:
            None

        Returns:
            CounterFamily: ``HC64`` or ``LEGACY32``.

        Raises:
            None
        """
        return CounterFamily.LEGACY32 if self.hc_octets is False else CounterFamily.HC64


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """The policy a plan is evaluated with.

    Attributes:
        policy: Validated collection rules.
        keywords: Effective description keywords.
    """

    policy: Policy
    keywords: KeywordSet


def device_context(uow: MetadataUnitOfWork, device: Device) -> DeviceContext:
    """Return the device facts policy rules match on.

    Args:
        uow: The open unit of work.
        device: The device.

    Returns:
        DeviceContext: Name, address, site name, tag and group names.

    Raises:
        None
    """
    site = uow.sites.get(device.site_id) if device.site_id is not None else None
    return DeviceContext(
        device.name, device.poll_address, site.name if site else None,
        tag_names(uow, device.tags), group_names(uow, device.groups),
    )  # fmt: skip


def _annotations(interface: Interface, tags: tuple[str, ...]) -> tuple[Annotations, Circuit]:
    """Map a stored annotation to policy annotations; defaults count as unset.

    Args:
        interface: The interface.
        tags: Names of its operator tags.

    Returns:
        tuple[Annotations, Circuit]: Annotations and circuit metadata.

    Raises:
        None
    """
    a = interface.annotation
    annotations = Annotations(
        interest=InterestClass.PRIMARY if a.interest is InterestClass.PRIMARY else None,
        sticky=True if a.sticky else None, wan_circuit=True if a.is_wan_circuit else None,
        display_rank=a.display_rank, tags=tags,
    )  # fmt: skip
    return annotations, Circuit(a.provider, a.circuit_id, a.contracted_bps)


def stored_facts(
    ctx: DeviceContext, interface: Interface | IdentityReview, tags: tuple[str, ...] = ()
) -> InterfaceFacts:
    """Build policy facts from a stored interface or a review's observed row.

    Args:
        ctx: The device context.
        interface: The interface, or the review holding an unresolved row.
        tags: Names of the interface's operator tags.

    Returns:
        InterfaceFacts: Facts without admin/oper status, which are not stored.

    Raises:
        None
    """
    annotations, circuit = (
        _annotations(interface, tags) if isinstance(interface, Interface) else (None, None)
    )
    return InterfaceFacts(
        device_name=ctx.name, device_address=ctx.address, site=ctx.site,
        device_tags=ctx.tags, device_groups=ctx.groups, if_index=interface.if_index,
        if_name=interface.if_name or None, if_descr=interface.if_descr or None,
        if_alias=interface.if_alias, if_type=interface.if_type, addresses=interface.addresses,
        physical=is_physical(interface.if_type, interface.if_name or None),
        annotations=annotations or Annotations(), circuit=circuit or Circuit(),
    )  # fmt: skip


def _entry(
    if_index: int, interface_id: InterfaceId | None, check: bool, collect: bool,
    caps: Capabilities,
) -> InterfacePlan:  # fmt: skip
    """Build one plan entry.

    Args:
        if_index: Current ifIndex.
        interface_id: The identity, or ``None`` while unresolved.
        check: Read status this cycle.
        collect: Policy wants counters.
        caps: Probed capabilities.

    Returns:
        InterfacePlan: The entry.

    Raises:
        None
    """
    return InterfacePlan(
        if_index, interface_id, check_status=check, collect=collect and check, family=caps.family,
        hc_packets=caps.hc_packets is not False, discontinuity=caps.discontinuity is not False,
    )  # fmt: skip


def build_plan(
    uow: MetadataUnitOfWork, device: Device, inputs: PlanInputs, caps: Mapping[int, Capabilities]
) -> PollPlan | None:
    """Plan one device's polls from stored identities and open reviews.

    Args:
        uow: The open unit of work.
        device: The device.
        inputs: Policy and keywords.
        caps: Probed capabilities by ifIndex.

    Returns:
        PollPlan | None: The plan, or ``None`` when nothing was ever discovered.

    Raises:
        StorageError: If a list exceeds its bound.
    """
    interfaces, reviews = device_interfaces(uow, device.id), open_reviews(uow, device.id)
    if not interfaces and not reviews:
        return None
    ctx, names = device_context(uow, device), _TagNames(uow)
    held = {c for r in reviews for c in r.candidates}
    entries: dict[int, InterfacePlan] = {}
    for i in interfaces:
        if i.if_index is None or i.lifecycle is InterfaceLifecycle.RETIRED or i.id in held:
            continue
        facts = stored_facts(ctx, i, names.of(i.tags))
        monitored = decide(facts, inputs.policy, inputs.keywords).monitored
        check = i.lifecycle is not InterfaceLifecycle.STALE
        entries[i.if_index] = _entry(i.if_index, i.id, check, monitored, _caps(caps, i.if_index))
    for r in reviews:
        # A review wins an ifIndex collision: no observation may reach a doubtful identity.
        monitored = decide(stored_facts(ctx, r), inputs.policy, inputs.keywords).monitored
        entries[r.if_index] = _entry(r.if_index, None, True, monitored, _caps(caps, r.if_index))
    return PollPlan(tuple(entries[k] for k in sorted(entries)))


def _caps(caps: Mapping[int, Capabilities], if_index: int) -> Capabilities:
    """Return the probed capabilities of an ifIndex, or unknown ones.

    Args:
        caps: Probed capabilities by ifIndex.
        if_index: The ifIndex.

    Returns:
        Capabilities: Known or all-unknown capabilities.

    Raises:
        None
    """
    return caps.get(if_index, _UNKNOWN)


_UNKNOWN = Capabilities()


class _TagNames:
    """Tag names by ID, read once per plan."""

    def __init__(self, uow: MetadataUnitOfWork) -> None:
        """Start with an empty cache.

        Args:
            uow: The open unit of work.

        Returns:
            None

        Raises:
            None
        """
        self._uow = uow
        self._names: dict[TagId, str | None] = {}

    def of(self, ids: frozenset[TagId]) -> tuple[str, ...]:
        """Return the names of tags, sorted.

        Args:
            ids: Tag IDs.

        Returns:
            tuple[str, ...]: Names of the tags that exist.

        Raises:
            None
        """
        for tag_id in ids - self._names.keys():
            tag = self._uow.tags.get(tag_id)
            self._names[tag_id] = None if tag is None else tag.name
        return tuple(sorted(n for i in ids if (n := self._names[i]) is not None))
