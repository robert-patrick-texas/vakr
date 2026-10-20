"""Collectors: the code that measures devices and produces observations.

Each subpackage turns one protocol's exchanges into canonical observations
(blueprint 6.1): ``snmp`` for Stage 1/Stage 2 SNMP polling, and ``icmp`` for
reachability. Collectors talk to the network only through ports, so they
can be tested against simulators.

Dependencies:
    vakr.ports: Network ports and the clock.

Safety:
    Collector or storage trouble is never reported as target-down
    (invariants 2 and 3).

Example:
    >>> import vakr.collectors
    >>> vakr.collectors.__name__
    'vakr.collectors'
"""
