"""The Compact collector service, ``vakr-collector.service`` (6.2, SVC-001..009, M3).

It wires the merged parts into one headless process: the scheduler polls
every device with the ICMP and SNMP collectors, rates are derived against
checkpointed baselines, every observation goes through the durable spool,
ingestion writes the SQLite telemetry store, discovery runs beside polling,
and timers keep health, checkpoints, and the store current.

Modules:
    inventory: The ``InventorySource`` contract and the merge seam.
    timings: Appendix E.27 cadences and bounds.
    preflight, build, identity: Startup checks and construction (16.3).
    icmp_step, snmp_step, snmp_parts: The scheduler's steps (9.2).
    plans, capacity, baselines, derive, checkpoint: Plans and rates (9.7).
    discovery, discovery_due: First discovery and rediscovery (DISC-001, DISC-002).
    pipeline: Outbox to spool (STORE-004).
    health, tally, maintenance: Self-observability and telemetry upkeep.
    replan, reload: Inventory changes and SIGHUP (CONF-003).
    service, shutdown: The run loop and 16.4 shutdown.
    once: ``vakr collector once``, one diagnostic poll.

Dependencies:
    vakr.scheduler, vakr.collectors, vakr.ingest, vakr.adapters: The parts.

Safety:
    No SQL here; blocking calls run in worker threads; every queue is bounded.

Example:
    $ sudo systemctl start vakr-collector.service
"""
