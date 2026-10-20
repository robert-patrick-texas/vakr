"""Collectors: turn raw protocol results into canonical observations (6.1, 9.1).

A collector sits between an adapter that talks to the network and the
scheduler that plans polls. It applies the measurement rules in
``vakr.logic`` to what the adapter measured and returns domain
observations, so the math lives in one place and every adapter feeds it
the same way.

- ``vakr.collectors.icmp``: ICMP probe groups to ``IcmpObservation``.

Dependencies:
    vakr.ports, vakr.logic, vakr.domain: Adapter contracts, rules, records.

Safety:
    Collectors never touch storage; they hand observations back to the
    caller, which owns the spool.

Example:
    >>> import vakr.collectors.icmp
    >>> vakr.collectors.icmp.__name__
    'vakr.collectors.icmp'
"""
