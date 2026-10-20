"""Performance harness: reproducible benchmarks behind sizing decisions (blueprint 23).

Benchmark scripts are named ``bench_*.py`` so pytest never collects them; the
small ``test_*.py`` files beside them check the generators and helpers so a
re-run measures the same workload the published report describes.

Dependencies:
    None.

Example:
    $ uv run pytest tests/performance
"""
