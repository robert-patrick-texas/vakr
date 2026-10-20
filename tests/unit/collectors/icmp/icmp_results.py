"""Builders of synthetic ``ProbeResult`` values for ICMP collector tests.

A compact notation keeps each test's input readable: an integer is a reply
with that RTT in microseconds, ``-1`` a timeout, ``"u"`` a router's
unreachable, ``"n"`` a probe the collector could not send (overload), and
``"a"`` a probe the collector stopped watching. The module name is unique
on purpose: helpers are never imported from a ``conftest``.

Dependencies:
    vakr.ports.icmp: Result types.

Example:
    >>> make_result([900, -1, "u"]).raw_rtts_us
    (900, -1, -1)
"""

from datetime import UTC, datetime

from vakr.domain.ids import CollectorId, DeviceId, PollId, new_id
from vakr.ports.icmp import CollectorFault, ProbeOutcome, ProbeRequest, ProbeResult, ProbeSample

START = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
_FAULTY = {"n": ProbeOutcome.NOT_SENT, "a": ProbeOutcome.ABORTED}


def sample(index: int, spec: int | str) -> ProbeSample:
    """Build one sample from the compact notation.

    Args:
        index: Probe position.
        spec: RTT in microseconds, -1, ``"u"``, ``"n"``, or ``"a"``.

    Returns:
        ProbeSample: The sample.

    Raises:
        None
    """
    if spec == "u":
        return ProbeSample(
            index, ProbeOutcome.UNREACHABLE, sent_at=START, detail="host unreachable"
        )
    if isinstance(spec, str):
        fault = CollectorFault.OVERLOADED if spec == "n" else CollectorFault.SOCKET_ERROR
        return ProbeSample(index, _FAULTY[spec], fault=fault, detail="collector")
    if spec < 0:
        return ProbeSample(index, ProbeOutcome.TIMEOUT, sent_at=START)
    return ProbeSample(index, ProbeOutcome.REPLY, spec, START)


def make_result(
    specs: list[int | str],
    target: str = "192.0.2.1",
    source: str | None = None,
    fault: CollectorFault | None = None,
) -> ProbeResult:
    """Build a result in the compact notation.

    Args:
        specs: One entry per planned probe.
        target: Probed address.
        source: Pinned source.
        fault: Result-level fault; the first sample fault when ``None``.

    Returns:
        ProbeResult: The result.

    Raises:
        None
    """
    samples = tuple(sample(i, s) for i, s in enumerate(specs))
    first = next((s.fault for s in samples if s.fault is not None), None)
    request = ProbeRequest(target, len(specs), 0.2, 1.0, source)
    return ProbeResult(request, START, 1.0, samples, fault or first)


def ids() -> tuple[PollId, CollectorId, DeviceId]:
    """Return fresh poll, collector, and device IDs.

    Args:
        None

    Returns:
        tuple[PollId, CollectorId, DeviceId]: The IDs.

    Raises:
        None
    """
    return new_id(PollId), new_id(CollectorId), new_id(DeviceId)
