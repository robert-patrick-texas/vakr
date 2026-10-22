"""Operator words for admin operations: where one is, or how it ended (API-011, DISC-T03).

``vakr inventory rediscover --wait``, ``vakr inventory operations``, and the
overview's rediscovery events (12.2 item 7) say the same thing the same
way.

Dependencies:
    vakr.domain.operations: The record and its result.

Safety:
    Pure; operations hold no secret.

Example:
    >>> from vakr.domain.operations import OperationResult
    >>> result_words(OperationResult(24, new=1, missing=2, duration_ms=1800))
    '24 interfaces, 1 new, 0 renumbered, 2 missing, 0 identity reviews opened in 1.8 s'
"""

from vakr.domain.operations import Operation, OperationResult


def result_words(result: OperationResult) -> str:
    """Describe a rediscovery's result in one line.

    Args:
        result: The counts.

    Returns:
        str: Interfaces, identity changes, reviews, and duration; capability
        changes and incompleteness only when there are any.

    Raises:
        None
    """
    words = (f"{result.interfaces} interfaces, {result.new} new, "
             f"{result.renumbered} renumbered, {result.missing} missing, "
             f"{result.reviews_opened} identity reviews opened")  # fmt: skip
    if result.capability_changes:
        words += f", {result.capability_changes} capability changes"
    if result.partial:
        words += " (partial: some table came back incomplete)"
    return f"{words} in {result.duration_ms / 1000:.1f} s"


def outcome_words(op: Operation) -> str:
    """Say where an operation is, or how it ended.

    Args:
        op: The operation.

    Returns:
        str: Such as ``queued``, ``succeeded: 24 interfaces, ...``, or ``failed: why``.

    Raises:
        None
    """
    if op.result is not None:
        return f"{op.state.value}: {result_words(op.result)}"
    if op.error is not None:
        return f"{op.state.value}: {op.error}"
    return op.state.value
