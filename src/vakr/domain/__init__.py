"""vakr's domain: network-monitoring concepts independent of storage and transport.

The domain names what an operator reasons about — devices, interfaces,
reachability, counters, freshness, and quality — as typed values. Collection
adapters, storage adapters, the API, the web UI, and the TUI all exchange
these types through the ports in ``vakr.ports``; none of them may import a
database driver or network library into this package (ARCH-001).

Dependencies:
    None beyond the standard library.

Safety:
    Domain types never carry credentials; devices refer to credentials by
    reference only (SEC-001).

Example:
    >>> from vakr.domain.enums import IcmpState
    >>> IcmpState("reachable") is IcmpState.REACHABLE
    True
"""
