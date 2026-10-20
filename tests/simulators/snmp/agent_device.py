"""One device of the in-process agent: its values, faults, and request handling.

GET, GETNEXT, and GETBULK follow RFC 3416: a missing instance answers
``noSuchInstance`` (``noSuchObject`` when the whole column is absent), the end
of the data answers ``endOfMibView``, and GETBULK returns non-repeaters once
and then up to max-repetitions rows. ``tooBig`` has an empty varbind list, as
RFC 3416 section 4.2.1 requires.

Dependencies:
    bisect: OID lookups in a sorted list.
    pysnmp.proto: Value types and the v2c PDU API.
    tests.simulators.snmp.snmprec: ``Record`` and wrap points.

Example:
    >>> d = AgentDevice([Record((1, 3, 6, 1, 2, 1, 1, 3, 0), "67", "100", rate=100)], lambda: 0.0)
    >>> d.number((1, 3, 6, 1, 2, 1, 1, 3, 0))
    100
"""

import bisect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pysnmp.proto import rfc1902, rfc1905
from pysnmp.proto.api import v2c as V2C  # noqa: N812

from tests.simulators.snmp.snmprec import WRAP, Oid, Record

_TYPES: dict[str, Callable[[str], Any]] = {
    "2": lambda v: rfc1902.Integer32(int(v)),
    "4": lambda v: rfc1902.OctetString(v.encode()),
    "4x": lambda v: rfc1902.OctetString(hexValue=v),
    "6": rfc1902.ObjectIdentifier,
    "64": rfc1902.IpAddress,
    "65": lambda v: rfc1902.Counter32(int(v)),
    "66": lambda v: rfc1902.Gauge32(int(v)),
    "67": lambda v: rfc1902.TimeTicks(int(v)),
    "70": lambda v: rfc1902.Counter64(int(v)),
}
_NUMERIC = ("65", "66", "67", "70", "2")


@dataclass(slots=True)
class Faults:
    """Misbehaviour to inject; the defaults are a well-behaved agent.

    Attributes:
        drop_all: Never answer.
        drop_next: Drop this many requests, then answer.
        drop_over: Drop requests carrying more varbinds than this.
        too_big_over: Answer tooBig when an answer would hold more varbinds.
        bulk_too_big: Apply ``too_big_over`` to GETBULK too, instead of
            truncating it as RFC 3416 asks.
        drop_subtrees: Drop requests touching these subtrees.
        error_subtrees: Answer this error status for these subtrees.
        delay_s: Seconds to wait before answering.
        truncated_copies: Undecodable copies to send before each answer.
        stranger_copy: Also send each answer from another socket first.
        looping: GETNEXT/GETBULK return the requested OID again.
    """

    drop_all: bool = False
    drop_next: int = 0
    drop_over: int | None = None
    too_big_over: int | None = None
    bulk_too_big: bool = False
    drop_subtrees: tuple[Oid, ...] = ()
    error_subtrees: dict[Oid, int] = field(default_factory=dict)
    delay_s: float = 0.0
    truncated_copies: int = 0
    stranger_copy: bool = False
    looping: bool = False


def _under(oid: Oid, roots: Sequence[Oid]) -> bool:
    """Report whether ``oid`` is at or below one of ``roots``.

    Args:
        oid: The OID.
        roots: Subtree roots.

    Returns:
        bool: True when some root is a prefix of ``oid``.

    Raises:
        None
    """
    return any(oid[: len(root)] == root for root in roots)


def _kind(pdu: Any) -> str:
    """Name a request PDU's type.

    Args:
        pdu: The request PDU.

    Returns:
        str: ``getbulk``, ``getnext``, or ``get``.

    Raises:
        None
    """
    if pdu.isSameTypeWith(V2C.GetBulkRequestPDU()):
        return "getbulk"
    return "getnext" if pdu.isSameTypeWith(V2C.GetNextRequestPDU()) else "get"


class AgentDevice:
    """The records one community serves.

    Attributes:
        faults: Misbehaviour to inject.
        requests: Requests received with this device's community.
        seen: Every request as ``(kind, oids)``; kind is ``get``, ``getnext``,
            or ``getbulk``.
        in_flight: Answers queued but not yet sent.
        max_in_flight: The most answers ever queued at once (SNMP-012).
    """

    def __init__(self, records: Sequence[Record], clock: Callable[[], float]) -> None:
        """Load the records.

        Args:
            records: The device's records.
            clock: Seconds, for values with a rate.

        Returns:
            None

        Raises:
            None
        """
        self._clock = clock
        self._epoch = clock()
        self._records = {record.oid: record for record in records}
        self._oids = sorted(self._records)
        self.faults = Faults()
        self.requests = 0
        self.seen: list[tuple[str, tuple[Oid, ...]]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def begin(self) -> None:
        """Count one answer as queued.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)

    def end(self) -> None:
        """Count one queued answer as sent.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.in_flight -= 1

    def set(self, record: Record) -> None:
        """Add or replace one record, for example a new discontinuity time.

        Args:
            record: The record.

        Returns:
            None

        Raises:
            None
        """
        if record.oid not in self._records:
            bisect.insort(self._oids, record.oid)
        self._records[record.oid] = record

    def remove(self, root: Oid) -> None:
        """Delete every record at or below ``root``.

        Args:
            root: The subtree.

        Returns:
            None

        Raises:
            None
        """
        self._oids = [oid for oid in self._oids if not _under(oid, (root,))]
        self._records = {oid: self._records[oid] for oid in self._oids}

    def restart(self) -> None:
        """Reload: ``sysUpTime`` and every advancing counter start again from 0.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._epoch = self._clock()
        for oid, record in self._records.items():
            if record.rate is not None:
                self._records[oid] = replace(record, value="0")

    def number(self, oid: Oid) -> int:
        """Return the current value of a numeric record.

        Args:
            oid: The record's OID.

        Returns:
            int: Initial value plus rate times elapsed time, wrapped.

        Raises:
            KeyError: If there is no such record.
        """
        record = self._records[oid]
        value = int(record.value)
        if record.rate is not None:
            value += int(record.rate * (self._clock() - self._epoch))
        return value % WRAP.get(record.tag, 2**63)

    def handle(self, message: Any) -> Any | None:
        """Build the answer to a request message, or ``None`` to drop it.

        Args:
            message: The decoded request.

        Returns:
            Any | None: The response message, or ``None``.

        Raises:
            None
        """
        self.requests += 1
        pdu = V2C.apiMessage.get_pdu(message)
        oids = [tuple(oid) for oid, _ in V2C.apiPDU.get_varbinds(pdu)]
        self.seen.append((_kind(pdu), tuple(oids)))
        if self._dropped(oids):
            return None
        response = V2C.apiMessage.get_response(message)
        out = V2C.apiMessage.get_pdu(response)
        status, index, varbinds = self._answer(pdu, oids)
        V2C.apiPDU.set_varbinds(out, varbinds)
        V2C.apiPDU.set_error_status(out, status)
        V2C.apiPDU.set_error_index(out, index)
        return response

    def _dropped(self, oids: list[Oid]) -> bool:
        """Decide whether to drop a request.

        Args:
            oids: The request's OIDs.

        Returns:
            bool: True to stay silent.

        Raises:
            None
        """
        f = self.faults
        if f.drop_next > 0:
            f.drop_next -= 1
            return True
        too_many = f.drop_over is not None and len(oids) > f.drop_over
        return f.drop_all or too_many or any(_under(oid, f.drop_subtrees) for oid in oids)

    def _answer(self, pdu: Any, oids: list[Oid]) -> tuple[int, int, list[Any]]:
        """Compute status, index, and varbinds for a request.

        Args:
            pdu: The request PDU.
            oids: Its OIDs.

        Returns:
            tuple[int, int, list[Any]]: Error status, error index, varbinds.

        Raises:
            None
        """
        for position, oid in enumerate(oids, start=1):
            for root, status in self.faults.error_subtrees.items():
                if _under(oid, (root,)):
                    return status, position, [(o, V2C.null) for o in oids]
        bulk = pdu.isSameTypeWith(V2C.GetBulkRequestPDU())
        if bulk:
            varbinds = self._bulk(pdu, oids)
        elif pdu.isSameTypeWith(V2C.GetNextRequestPDU()):
            varbinds = [self._next(oid) for oid in oids]
        else:
            varbinds = [(oid, self._get(oid)) for oid in oids]
        limit = self.faults.too_big_over
        if limit is not None and len(varbinds) > limit:
            if bulk and not self.faults.bulk_too_big:
                return 0, 0, varbinds[:limit]
            return 1, 0, []
        return 0, 0, varbinds

    def _bulk(self, pdu: Any, oids: list[Oid]) -> list[Any]:
        """Answer a GETBULK: non-repeaters once, then repeated rows.

        Args:
            pdu: The GETBULK PDU.
            oids: Its OIDs.

        Returns:
            list[Any]: The varbinds.

        Raises:
            None
        """
        skip = int(V2C.apiBulkPDU.get_non_repeaters(pdu))
        reps = int(V2C.apiBulkPDU.get_max_repetitions(pdu))
        varbinds = [self._next(oid) for oid in oids[:skip]]
        cursors = oids[skip:]
        for _ in range(reps):
            row = [self._next(oid) for oid in cursors]
            varbinds += row
            cursors = [tuple(oid) for oid, _ in row]
            if all(isinstance(value, rfc1905.EndOfMibView) for _, value in row):
                break
        return varbinds

    def _get(self, oid: Oid) -> Any:
        """Return the value at ``oid``, or the right exception value.

        Args:
            oid: The requested OID.

        Returns:
            Any: A value, ``noSuchInstance``, or ``noSuchObject``.

        Raises:
            None
        """
        record = self._records.get(oid)
        if record is None:
            at = bisect.bisect_left(self._oids, oid[:-1])
            column = at < len(self._oids) and self._oids[at][: len(oid) - 1] == oid[:-1]
            return rfc1905.noSuchInstance if column else rfc1905.noSuchObject
        if record.tag in _NUMERIC and record.rate is not None:
            return _TYPES[record.tag](str(self.number(oid)))
        return _TYPES[record.tag](record.value)

    def _next(self, oid: Oid) -> tuple[Oid, Any]:
        """Return the first record after ``oid``, or ``endOfMibView``.

        Args:
            oid: The OID to start after.

        Returns:
            tuple[Oid, Any]: The next OID and its value.

        Raises:
            None
        """
        if self.faults.looping and oid in self._records:
            return oid, self._get(oid)
        at = bisect.bisect_right(self._oids, oid)
        if at >= len(self._oids):
            return oid, rfc1905.endOfMibView
        found = self._oids[at]
        return found, self._get(found)
