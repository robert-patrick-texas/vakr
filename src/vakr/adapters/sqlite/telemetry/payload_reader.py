"""Sequential reading of a packed rollup payload (``rollup_codec``).

The payload format is described in ``rollup_codec``; this reader knows only
its primitives: raw bytes, unsigned LEB128 varints, and the mode byte that
says how each of a metric's four aggregates is stored (0: ``0.0``, 1: a
varint, 2: a big-endian float32, 3: a big-endian float64).

Dependencies:
    struct: float32 and float64 unpacking.
    vakr.domain.errors: ``StorageError`` for a damaged payload.

Safety:
    Every read is bounds-checked; running past the end is ``StorageError``.

Example:
    >>> r = PayloadReader(bytes([0x96, 0x01]), 0)
    >>> r.varint(), r.done()
    (150, True)
"""

import struct

from vakr.domain.errors import StorageError

_F32 = struct.Struct(">f")
_F64 = struct.Struct(">d")


class PayloadReader:
    """Sequential reader over a payload.

    Attributes:
        None beyond the private buffer and position.
    """

    def __init__(self, data: bytes, position: int) -> None:
        """Start reading ``data`` at ``position``.

        Args:
            data: The payload.
            position: First byte to read.

        Returns:
            None

        Raises:
            None
        """
        self._data, self._pos = data, position

    def _truncated(self) -> StorageError:
        """Build the error for a payload that ends too early.

        Args:
            None

        Returns:
            StorageError: The error to raise.

        Raises:
            None
        """
        return StorageError("a stored rollup payload is truncated", retryable=False)

    def varint(self) -> int:
        """Read one unsigned LEB128 integer.

        Args:
            None

        Returns:
            int: The integer.

        Raises:
            StorageError: If the payload ends inside it.
        """
        data, pos, value, shift = self._data, self._pos, 0, 0
        try:
            while True:
                byte = data[pos]
                pos += 1
                value |= (byte & 0x7F) << shift
                if byte < 0x80:
                    self._pos = pos
                    return value
                shift += 7
        except IndexError:
            raise self._truncated() from None

    def values(self) -> tuple[float, float, float, float]:
        """Read a mode byte and the four aggregates it describes.

        Args:
            None

        Returns:
            tuple[float, float, float, float]: Minimum, mean, p95, maximum.

        Raises:
            StorageError: If the payload ends early.
        """
        out = [0.0, 0.0, 0.0, 0.0]
        modes = self._byte()
        for i in range(4):
            mode = (modes >> (2 * i)) & 3
            if mode == 1:
                out[i] = float(self.varint())
            elif mode:
                size = 4 if mode == 2 else 8
                if self._pos + size > len(self._data):
                    raise self._truncated()
                out[i] = (_F32 if mode == 2 else _F64).unpack_from(self._data, self._pos)[0]
                self._pos += size
        return out[0], out[1], out[2], out[3]

    def skip_values(self) -> None:
        """Step over a mode byte and the aggregates it describes without decoding them.

        Args:
            None

        Returns:
            None

        Raises:
            StorageError: If the payload ends early.
        """
        modes = self._byte()
        for i in range(4):
            mode = (modes >> (2 * i)) & 3
            if mode == 1:
                self.varint()
            elif mode:
                self._pos += 4 if mode == 2 else 8
        if self._pos > len(self._data):
            raise self._truncated()

    def _byte(self) -> int:
        """Read one byte.

        Args:
            None

        Returns:
            int: The byte.

        Raises:
            StorageError: If the payload has ended.
        """
        if self._pos >= len(self._data):
            raise self._truncated()
        self._pos += 1
        return self._data[self._pos - 1]

    def done(self) -> bool:
        """Tell whether every byte was consumed.

        Args:
            None

        Returns:
            bool: ``True`` at the end of the payload.

        Raises:
            None
        """
        return self._pos == len(self._data)
