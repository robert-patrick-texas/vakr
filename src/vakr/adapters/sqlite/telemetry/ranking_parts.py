"""Ranking partials: what one interface's samples contribute to a ranking, exactly (E.17 #13, E.28).

A ``Partial`` holds everything ``WindowSummary`` needs from some of an
interface's samples, in a form that merges without losing anything:

- counts (samples, inbound and outbound rates, counted errors, packet
  samples) and integer error sums, which add;
- every available ``in_bps`` and ``out_bps`` value, because the window's
  nearest-rank p95 (``vakr.logic.stats.p95``) needs them all;
- the packet sum as float parts that ``math.fsum`` joins into the correctly
  rounded total, whatever the split (``vakr.logic.stats.exact_parts``).

Native scans (``rankings.scan``) fold in with ``add_row``: SQLite returns
each group's rates and packet products as text (SQLite 3.53 writes a double with 17 significant
digits, so the text round-trips exactly). Projected buckets fold in with
``add_payload``, the packed form ``rank_iface`` stores (``payload``):
version 1, then the counts and sums, then the sorted rates and the exact
packet parts as little-endian float64. Rates are held as ``array('d')``,
8 bytes each, so a 24-hour window at MVP scale (about 2.3 million samples
per collector) needs about 37 MB while it is ranked.

Dependencies:
    array, math, struct, sys: Compact buffers, exact sums, packing, byte order.
    vakr.logic.stats: The shared nearest-rank p95 and exact parts.
    vakr.ports.telemetry.rankings: The summary type.

Safety:
    Pure. A payload of an unknown version or length is refused, never guessed.

Example:
    >>> part = Partial()
    >>> part.add_row((3, "2.5,1.5", None, 2, 7, 0, 2, "60000000.0,30000000.0"))
    >>> again = Partial()
    >>> again.add_payload(part.payload())
    >>> list(again.ins), again.samples, again.in_errors, math.fsum(again.parts)
    ([1.5, 2.5], 3, 7, 90000000.0)
"""

import math
import struct
import sys
from array import array
from dataclasses import dataclass, field

from vakr.domain.errors import StorageError
from vakr.domain.ids import InterfaceId
from vakr.logic.stats import exact_parts, p95
from vakr.ports.telemetry.rankings import WindowSummary

_US = 1_000_000
_VERSION = 1
_HEADER = struct.Struct("<BIIIIIHqq")
_SIZE, _UNPACK = _HEADER.size, _HEADER.unpack_from
_SWAP = sys.byteorder != "little"


def _floats() -> array:
    """Return an empty float64 buffer.

    Args:
        None

    Returns:
        array: ``array('d')``.

    Raises:
        None
    """
    return array("d")


def _bytes(values: array) -> bytes:
    """Return a float64 buffer as little-endian bytes.

    Args:
        values: The buffer.

    Returns:
        bytes: 8 bytes per value.

    Raises:
        None
    """
    if _SWAP:
        values = array("d", values)
        values.byteswap()
    return values.tobytes()


def _extend(target: array, data: bytes) -> None:
    """Append little-endian float64 bytes to a buffer on a big-endian host.

    Args:
        target: The buffer.
        data: 8 bytes per value.

    Returns:
        None

    Raises:
        None
    """
    chunk = array("d")
    chunk.frombytes(data)
    chunk.byteswap()
    target.extend(chunk)


@dataclass(slots=True)
class Partial:
    """One interface's ranking partial over some of its samples.

    Attributes:
        samples: Observations seen.
        ins: Available inbound rates.
        outs: Available outbound rates.
        counted: Observations with both error deltas.
        in_errors: Their summed inbound deltas.
        out_errors: Their summed outbound deltas.
        with_packets: Counted observations that also have packet rates and elapsed time.
        parts: Floats whose exact sum is the sum of ``(in_pps + out_pps) x elapsed_us``
            over those.
    """

    samples: int = 0
    ins: array = field(default_factory=_floats)
    outs: array = field(default_factory=_floats)
    counted: int = 0
    in_errors: int = 0
    out_errors: int = 0
    with_packets: int = 0
    parts: array = field(default_factory=_floats)

    def add_row(self, row: tuple) -> None:
        """Fold one native arm's row (without its series key) in.

        Args:
            row: ``(count, in text, out text, counted, in errors, out errors,
                with packets, packet products text)``.

        Returns:
            None

        Raises:
            ValueError: If a stored value is not a number (damaged storage).
        """
        n, ins, outs, counted, in_err, out_err, with_packets, products = row
        self.samples += n
        if ins:
            self.ins.extend(map(float, ins.split(",")))
        if outs:
            self.outs.extend(map(float, outs.split(",")))
        if products:
            self.parts.extend(map(float, products.split(",")))
        self.counted += counted
        self.in_errors += in_err
        self.out_errors += out_err
        self.with_packets += with_packets

    def add_payload(self, payload: bytes) -> None:
        """Fold one stored projection payload in.

        Args:
            payload: Bytes written by ``payload``.

        Returns:
            None

        Raises:
            StorageError: If the payload has another version or length (damaged storage).
        """
        size = len(payload)
        if size < _SIZE or payload[0] != _VERSION:
            raise StorageError("a ranking projection payload is damaged", retryable=False)
        _v, samples, n_in, n_out, counted, with_packets, n_parts, in_err, out_err = _UNPACK(payload)
        mid = _SIZE + 8 * n_in
        end = mid + 8 * n_out
        if size != end + 8 * n_parts:
            raise StorageError("a ranking projection payload is damaged", retryable=False)
        if _SWAP:
            _extend(self.ins, payload[_SIZE:mid])
            _extend(self.outs, payload[mid:end])
            _extend(self.parts, payload[end:])
        else:  # the usual little-endian host: copy the bytes straight in
            self.ins.frombytes(payload[_SIZE:mid])
            self.outs.frombytes(payload[mid:end])
            self.parts.frombytes(payload[end:])
        self.samples += samples
        self.counted += counted
        self.in_errors += in_err
        self.out_errors += out_err
        self.with_packets += with_packets

    def payload(self) -> bytes:
        """Pack this partial for ``rank_iface``: rates sorted, packet sum as exact parts.

        Args:
            None

        Returns:
            bytes: Version 1 payload.

        Raises:
            struct.error: If a count or sum does not fit its field.
        """
        ins, outs = array("d", sorted(self.ins)), array("d", sorted(self.outs))
        parts = array("d", exact_parts(self.parts))
        head = _HEADER.pack(
            _VERSION,
            self.samples,
            len(ins),
            len(outs),
            self.counted,
            self.with_packets,
            len(parts),
            self.in_errors,
            self.out_errors,
        )
        return head + _bytes(ins) + _bytes(outs) + _bytes(parts)  # fmt: skip

    def summary(self, interface_id: InterfaceId) -> WindowSummary:
        """Return the port's summary of everything folded in.

        Args:
            interface_id: The interface the series key names.

        Returns:
            WindowSummary: Counts, p95 rates, and error totals.

        Raises:
            None
        """
        packets = None
        if self.counted and self.with_packets == self.counted:
            packets = round(math.fsum(self.parts) / _US)
        return WindowSummary(
            interface_id, self.samples, len(self.ins), len(self.outs),
            p95(self.ins.tolist()) if self.ins else None,
            p95(self.outs.tolist()) if self.outs else None,
            self.counted, self.in_errors, self.out_errors, packets,
        )  # fmt: skip
