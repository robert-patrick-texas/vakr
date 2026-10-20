"""Composition roots: where vakr's ports, adapters, and logic become running services.

Every other package is a part with one job: the scheduler plans polls, the
collectors speak ICMP and SNMP, the spool keeps observations safe, and the
SQLite adapters store them. A package under ``vakr.app`` is the only place
that knows which concrete adapter fills which port for one service, so the
parts never import each other's adapters (blueprint 6.5, 17.1).

Modules:
    collector: ``vakr-collector.service``, the Compact headless collector (6.2).

Dependencies:
    vakr.app.collector: The collector composition root.

Safety:
    Composition only; SQL stays in the adapters and blocking calls run in
    worker threads.

Example:
    >>> import vakr.app.collector
    >>> vakr.app.collector.__name__
    'vakr.app.collector'
"""
