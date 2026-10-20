"""Unit tests for ``vakr.scheduler``: planning, admission, the poll state machine, and the driver.

The asyncio tests run on a virtual-time event loop (``sched_simloop``), so a
simulated day of polling takes seconds and every timing is exact.

Dependencies:
    pytest; vakr.scheduler.

Example:
    $ uv run pytest tests/unit/scheduler
"""
