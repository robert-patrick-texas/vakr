"""A text stream of named members, for exports that travel inside a field bundle.

Field bundles cap each member's size, and an export run under another
account cannot write into the field kit's private directory. So an export
writes one stream to standard output that names each member before its
lines, and the field kit splits it into bundle members::

    #member-stream 1
    @member NAME KIND
    ...lines...
    @end

``KIND`` is ``table`` (CSV with a header row, which the field kit
pseudonymizes column by column) or ``text``. A content line that starts
with ``@`` is written with one more ``@``.

Dependencies:
    typing: ``TextIO``.

Safety:
    Writes only to the stream it is given.

Example:
    >>> import io
    >>> out = io.StringIO()
    >>> stream = MemberStream(out)
    >>> stream.member("a.csv", "table"); stream.line("@x"); stream.end()
    >>> out.getvalue().splitlines()
    ['#member-stream 1', '@member a.csv table', '@@x', '@end']
"""

from typing import TextIO

STREAM_HEADER = "#member-stream 1"


class MemberStream:
    """Writes members as ``@member NAME KIND`` followed by their lines."""

    def __init__(self, out: TextIO) -> None:
        """Start the stream.

        Args:
            out: Text output.

        Returns:
            None

        Raises:
            OSError: If the output cannot be written.
        """
        self._out = out
        out.write(STREAM_HEADER + "\n")

    def member(self, name: str, kind: str) -> None:
        """Begin a member.

        Args:
            name: Relative member path, such as ``rates/d0001.csv``.
            kind: ``table`` (CSV with a header) or ``text``.

        Returns:
            None

        Raises:
            OSError: If the output cannot be written.
        """
        self._out.write(f"@member {name} {kind}\n")

    def line(self, text: str) -> None:
        """Write one content line, escaping a leading ``@``.

        Args:
            text: The line, without its newline.

        Returns:
            None

        Raises:
            OSError: If the output cannot be written.
        """
        self._out.write(("@" + text if text.startswith("@") else text) + "\n")

    def end(self) -> None:
        """Close the stream.

        Args:
            None

        Returns:
            None

        Raises:
            OSError: If the output cannot be written.
        """
        self._out.write("@end\n")
        self._out.flush()
