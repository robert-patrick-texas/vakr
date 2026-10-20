"""Unit tests for ``vakr.logic``: the measurement rules behind every operator-visible state.

These rules decide outage versus false alarm and gap versus spike, so they
carry the strictest gate in the project: at least 95% branch coverage
(QG-006) plus property-based tests where a table cannot cover the space.
Property tests run derandomized, so CI results are reproducible.

Dependencies:
    pytest, hypothesis: Table-driven and property-based tests.

Example:
    $ uv run pytest --cov=vakr.logic --cov-branch tests/unit/logic
"""
