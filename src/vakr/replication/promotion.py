"""Promote a peer, demote a primary, and find two primaries (MPOS-003, MPOS-006, ADR-024, E.39).

``vakr node promote`` turns a peer into the metadata primary when the
primary is lost, and ``vakr node demote`` turns a returning old primary
into a peer of its successor. This module decides; ``vakr.cli.cmd_node``
acts. The decisions:

- **promote** refuses on a primary; reports the newest successful
  metadata pull and its age; refuses without ``--force`` when no pull
  ever succeeded (the node holds no copy of the primary's metadata), and
  when the old primary's API still answers as a primary, or when it
  cannot be told whether it does. A partner that answers as a peer was
  demoted first (a planned swap): promoting is then safe. An unreachable
  one (name resolution, TCP, or the proxy's CONNECT failed) is the case
  the command exists for.
- **demote** refuses on a peer, and without a URL for the new primary;
  it never needs ``--force``, because a peer cannot edit metadata.
- **two primaries**: a primary whose partner's observation state says
  ``primary`` too reports CRITICAL node health, naming which one looks
  like the old primary. It never touches a target's state (MPOS-006).

Dependencies:
    vakr.replication.client: The partner client and its errors.
    vakr.config.node: The node settings.

Safety:
    Messages name URLs, positions, and times, never the token.

Example:
    >>> promoted(NodeSettings("peer", "west", "https://east.example.net"))["peer_url"]
    'https://east.example.net'
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vakr.config.node import PEER, PRIMARY, NodeSettings
from vakr.query.text import age_text
from vakr.replication.client import EgressError, PartnerError, PartnerReader
from vakr.replication.status import PullStatus
from vakr.replication.token_file import TokenFileError

UNREACHABLE_STEPS = frozenset({"dns", "tcp", "proxy"})
"""Egress steps whose failure means the partner cannot be reached at all."""
DOC = "docs/operations/two-positions.md#losing-the-primary"


@dataclass(frozen=True, slots=True)
class PartnerProbe:
    """What the partner's ``metadata/state`` answered just now.

    Attributes:
        url: Where it was asked.
        answered: ``True`` its API answered; ``False`` unreachable; ``None`` cannot tell.
        role: The role it reported, if it answered a state.
        position: The position it reported.
        detail: Operator words for the outcome.
    """

    url: str
    answered: bool | None
    role: str | None = None
    position: str | None = None
    detail: str = ""


@dataclass(slots=True)
class Decision:
    """Whether a role change may go ahead, and what was found on the way.

    Attributes:
        lines: The preconditions found, for the operator.
        refusals: Why it may not, each naming the remedy; empty when it may.
        forced: Refusals ``--force`` overrode.
    """

    lines: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    forced: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """Whether the change may go ahead.

        Args:
            None

        Returns:
            bool: ``True`` without refusals.

        Raises:
            None
        """
        return not self.refusals


def probe_partner(url: str, client_of: Callable[[str], PartnerReader]) -> PartnerProbe:
    """Ask the partner's ``metadata/state`` once, within the client's 20 s timeout.

    Args:
        url: The partner's base URL; empty means none is configured.
        client_of: Builds the client (token file and egress settings) for a URL.

    Returns:
        PartnerProbe: The outcome; never raises for partner trouble.

    Raises:
        None
    """
    if not url:
        return PartnerProbe(url, None, detail="no partner URL is configured")
    try:
        doc = client_of(url).get_json("metadata/state")
    except PartnerError as exc:
        return PartnerProbe(url, True, detail=f"its API answered: {exc}")
    except EgressError as exc:
        reachable = None if exc.step not in UNREACHABLE_STEPS else False
        return PartnerProbe(url, reachable, detail=str(exc))
    except (TokenFileError, OSError, ValueError) as exc:  # the token file or egress settings
        return PartnerProbe(url, None, detail=f"cannot ask it: {exc}")
    doc = doc if isinstance(doc, dict) else {}
    role, where = doc.get("role"), doc.get("position")
    role = role if role in (PRIMARY, PEER) else None
    where = where if isinstance(where, str) and where else None
    return PartnerProbe(url, True, role, where, f"answers as {role or 'an unknown role'}")


def promote_decision(node: NodeSettings, pull: PullStatus | None, probe: PartnerProbe, *,
                     force: bool, now: datetime) -> Decision:  # fmt: skip
    """Check promotion's preconditions on this node.

    Args:
        node: This node's settings.
        pull: Its metadata pull record, or ``None``.
        probe: What the primary answered just now.
        force: ``--force``: override the refusals that concern the old primary.
        now: Current time (UTC).

    Returns:
        Decision: The preconditions and any refusals.

    Raises:
        None
    """
    found = Decision()
    if not node.is_peer:
        found.refusals.append("this node is already a primary (node.role = primary); "
                              "only a peer can be promoted")  # fmt: skip
        return found
    age = None if pull is None else pull.success_age_s(now)
    if pull is None or age is None:
        found.lines.append("newest metadata pull: none has succeeded")
        _refuse(found, force, "this node never copied the primary's metadata, so as a primary "
                "it would start with no inventory or accounts; pass --force to promote it "
                "anyway")  # fmt: skip
    else:
        found.lines.append(f"newest metadata pull: {pull.last_success_at} "
                           f"({age_text(age)} ago); edits made on the old primary after it "
                           "are not on this node")  # fmt: skip
    found.lines.append(f"old primary {probe.url}: {probe.detail}")
    if probe.answered is False or (probe.answered and probe.role == PEER):
        return found
    if probe.answered:
        where = f" (position {probe.position})" if probe.position else ""
        _refuse(found, force, f"the primary at {probe.url}{where} still answers: promoting now "
                "makes two primaries that each accept edits. Demote it there first "
                "(`sudo vakr node demote`), cut it off, or pass --force if it is beyond "
                "use")  # fmt: skip
    else:
        _refuse(found, force, f"it cannot be told whether the primary at {probe.url} is up "
                "(its address answers, or the check could not run); pass --force once you "
                "know it is down")  # fmt: skip
    return found


def demote_decision(node: NodeSettings, url: str, probe: PartnerProbe) -> Decision:
    """Check demotion's preconditions on this node.

    Args:
        node: This node's settings.
        url: The new primary's URL (``--primary-url`` or ``node.peer_url``).
        probe: What it answered just now.

    Returns:
        Decision: The preconditions and any refusals.

    Raises:
        None
    """
    found = Decision()
    if node.is_peer:
        found.refusals.append(f"this node is already a peer of {node.primary_url}")
        return found
    if not url:
        found.refusals.append("name the new primary with --primary-url https://...; "
                              "node.peer_url is not set")  # fmt: skip
    if not node.position:
        found.refusals.append("a peer needs node.position; set it first with "
                              "`sudo vakr setup --position NAME`")  # fmt: skip
    if found.refusals:
        return found
    found.lines.append(f"new primary {url}: {probe.detail}")
    if probe.answered and probe.role == PEER:
        found.lines.append("it is still a peer: run `sudo vakr node promote` there; until then "
                           "this node's pulls are refused and it keeps its metadata")  # fmt: skip
    elif not (probe.answered and probe.role == PRIMARY):
        found.lines.append("this node becomes a peer now and resyncs at its first successful pull")
    return found


def _refuse(found: Decision, force: bool, why: str) -> None:
    """Add a refusal, or note it as overridden by ``--force``.

    Args:
        found: The decision, changed in place.
        force: Whether ``--force`` was given.
        why: The refusal.

    Returns:
        None

    Raises:
        None
    """
    (found.forced if force else found.refusals).append(why)


def promoted(node: NodeSettings) -> dict[str, str | None]:
    """Return the ``[node]`` changes of a promotion; the old primary becomes the partner.

    Args:
        node: The peer's settings.

    Returns:
        dict: Keys to set; ``None`` removes the key.

    Raises:
        None
    """
    return {"role": PRIMARY, "primary_url": None, "peer_url": node.primary_url or None}


def demoted(url: str) -> dict[str, str | None]:
    """Return the ``[node]`` changes of a demotion.

    Args:
        url: The new primary's URL.

    Returns:
        dict: Keys to set; ``None`` removes the key.

    Raises:
        None
    """
    return {"role": PEER, "primary_url": url, "peer_url": None}


def two_primaries(node: NodeSettings, partner: Mapping[str, Any] | None,
                  own_promoted_at: str | None) -> str | None:  # fmt: skip
    """Say why this node and its partner are both primaries, or ``None`` (E.39).

    Args:
        node: This node's settings.
        partner: ``partner.json``: what the partner's state reported last.
        own_promoted_at: When this node was promoted, if it was.

    Returns:
        str | None: The CRITICAL summary, naming the node to demote.

    Raises:
        None
    """
    if node.is_peer or not node.peer_url or not partner or partner.get("role") != PRIMARY:
        return None
    where = partner.get("position") or node.peer_url
    theirs = partner.get("promoted_at")
    if theirs and (not own_promoted_at or theirs > own_promoted_at):
        advice = f"{where} was promoted at {theirs}: this node looks like the old primary; " \
            "run `sudo vakr node demote` here"  # fmt: skip
    elif own_promoted_at:
        advice = f"this node was promoted at {own_promoted_at}: {where} looks like the old " \
            "primary; run `sudo vakr node demote` there"  # fmt: skip
    else:
        advice = "run `sudo vakr node demote` on the node whose metadata edits may be discarded"
    return f"two primaries: this node and {where} both have node.role = primary, and " \
        f"metadata edits on either diverge; {advice} ({DOC})"  # fmt: skip
