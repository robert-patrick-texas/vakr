"""Interface status records that ride with a replication page (schema 4, E.35, MPOS-004).

``repl_log`` admits only families 1 to 5, so a node serves each poll's interface
status records in the page's ``attachments`` member, with the seq of the poll's
SNMP device observation. ``verified_attachments`` checks them as strictly as the
page's observations: a seq inside the page, a status record, the right collector.
A partner that predates them sends no member, and one that cannot store them
ignores it.

Dependencies:
    vakr.domain: Envelopes, errors, and the status record.

Safety:
    Nothing from the partner is trusted unchecked.

Example:
    >>> verified_attachments({"next_after": 3}, None, 0)
    []
"""

from typing import Any

from vakr.domain.envelope import envelope_from_dict, open_envelope
from vakr.domain.errors import ValidationError
from vakr.domain.ids import CollectorId
from vakr.domain.observations import InterfaceStatusObservation, Observation


def verified_attachments(
    data: dict[str, Any], source: CollectorId, after: int
) -> list[Observation]:
    """Verify the interface status records a page carries with its SNMP entries (E.35).

    Args:
        data: The parsed ``observations`` answer.
        source: The collector asked for.
        after: The seq asked after.

    Returns:
        list[Observation]: The verified records; none from a partner without them.

    Raises:
        ValidationError: If an attachment is malformed, out of range, or not a status record.
    """
    found: list[Observation] = []
    for item in data.get("attachments") or []:
        seq = item.get("seq") if isinstance(item, dict) else None
        if not isinstance(seq, int) or seq <= after or seq > data["next_after"]:
            raise ValidationError("the partner's observation page has a stray attachment")
        observation = open_envelope(envelope_from_dict(item.get("envelope") or {}))
        if not isinstance(observation, InterfaceStatusObservation):
            raise ValidationError("the partner's page attaches something other than a status")
        if observation.collector_id != source:
            raise ValidationError("the partner's page holds another collector's observation")
        found.append(observation)
    return found
