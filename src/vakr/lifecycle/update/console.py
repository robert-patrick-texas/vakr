"""Operator confirmation for commands that change the system.

Self-update and CA trust changes ask before acting. Under a systemd timer or
in a pipeline there is no one to ask, so a missing terminal is treated as
"no" unless the operator already said yes with ``--yes``; a command never
proceeds by accident because its input happened to be empty.

Dependencies:
    sys: Terminal detection and prompts.

Safety:
    Only an explicit ``y`` or ``yes`` confirms.

Example:
    >>> confirmer(True)("Install?")
    True
"""

import sys
from collections.abc import Callable
from typing import TextIO

type Confirm = Callable[[str], bool]


def confirmer(assume_yes: bool, stdin: TextIO | None = None) -> Confirm:
    """Return a confirmation callback.

    Args:
        assume_yes: ``--yes`` was given, so every question is answered yes.
        stdin: Input stream; ``None`` means ``sys.stdin`` at call time.

    Returns:
        Confirm: Callable taking a question and returning ``bool``; without a
        terminal and without ``assume_yes`` it declines and says why.

    Raises:
        None
    """

    def confirm(question: str) -> bool:
        """Ask ``question`` unless ``--yes`` already answered it.

        Args:
            question: Prompt text.

        Returns:
            bool: Whether to proceed.

        Raises:
            None
        """
        if assume_yes:
            return True
        stream = stdin or sys.stdin
        if not stream.isatty():
            print("not a terminal; pass --yes to confirm", file=sys.stderr)
            return False
        print(f"{question} [y/N] ", end="", flush=True)
        return stream.readline().strip().lower() in {"y", "yes"}

    return confirm
