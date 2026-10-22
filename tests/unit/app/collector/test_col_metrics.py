"""The running collector writes its metrics snapshot, and a clean stop removes it (OBS-T01, E.33).

A real relocated install, an empty inventory: the service writes
``<run dir>/collector-metrics.json`` at start (mode 0640), the API's
metrics service reads it as fresh and valid and answers the collector's
families, and after a clean stop the file is gone, so the API reports
``missing`` at once rather than ``stale`` later.

Dependencies:
    tests.integration.collector.collector_lab: The install.
    vakr.app.collector: Build and service.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_metrics.py
"""

import asyncio
import time
from pathlib import Path

from tests.integration.collector.collector_lab import make_root
from tests.unit.metrics.metrics_testkit import parse
from vakr.app.collector.build import build_parts
from vakr.app.collector.service import CollectorService
from vakr.app.collector.timings import ServiceTimings
from vakr.metrics.service import MetricsService
from vakr.metrics.snapshot import SNAPSHOT_FILE, read_snapshot

FAST = ServiceTimings(checkpoint_interval_s=0.2, health_interval_s=0.2, change_poll_s=0.1)


def test_obs_t01_the_collector_writes_a_snapshot_the_api_reads(tmp_path: Path) -> None:
    """OBS-T01, E.33: written at start with mode 0640, valid for the API, removed at stop.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = make_root(tmp_path, 161)
    path = paths.run_dir / SNAPSHOT_FILE

    async def scenario() -> tuple[int, str, int]:
        """Run, scrape the snapshot while running, then stop.

        Args:
            None

        Returns:
            tuple: The file mode, the scrape text, and the exit code.

        Raises:
            None
        """
        service = CollectorService(await build_parts(paths), FAST)
        task = asyncio.create_task(service.run())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if path.exists():
                break
        mode = path.stat().st_mode & 0o777
        metrics = MetricsService(snapshot_path=path)
        text = metrics.render([], now_s=time.time())
        service.request_stop()
        return mode, text, await asyncio.wait_for(task, 20)

    mode, text, code = asyncio.run(scenario())
    assert (mode, code) == (0o640, 0)
    families = parse(text)
    assert families["vakr_collector_metrics_up"].values() == {(("reason", "ok"),): 1.0}
    state = families["vakr_collector_state"].values()
    assert sum(state.values()) == 1  # exactly one current state
    assert families["vakr_collector_devices_scheduled"].values() == {(): 0.0}
    assert {"vakr_collector_spool_bytes", "vakr_collector_polls_scheduled_total",
            "vakr_collector_upkeep_passes_total"} <= set(families)  # fmt: skip
    collector = [labels for _, labels, _ in families["vakr_process_open_fds"].samples]
    assert (("process", "collector"),) in collector
    assert not path.exists()
    assert read_snapshot(path, time.time()).reason == "missing"
