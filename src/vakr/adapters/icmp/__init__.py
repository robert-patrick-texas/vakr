"""ICMP adapter: Echo probes over Linux unprivileged ping sockets (ICMP-001..014).

``SocketIcmpProber`` implements ``vakr.ports.icmp.IcmpProber`` with
``socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)`` and its ICMPv6 twin, which need
no ``CAP_NET_RAW`` when ``net.ipv4.ping_group_range`` includes the
collector's group (ICMP-012, ADR-023). Probes to every target share one
socket per address family and source, on the asyncio event loop, with no
thread per probe.

- ``transport``: the prober, its in-flight cap, and the per-cycle deadline.
- ``pool``, ``channel``, ``book``, ``table``: sockets, reading, matching.
- ``wire``, ``wire_errors``, ``sockets``, ``base``: bytes, errnos, seams.
- ``sysctl``, ``health``: the ``ping_group_range`` preflight for ``vakr doctor``.

Dependencies:
    vakr.ports, vakr.domain: The contract and shared vocabulary.

Safety:
    Collector trouble (no permission, socket failure, overload) becomes
    ``CollectorFault`` evidence and ``UNKNOWN`` state, never target loss
    (invariant 2, OBS-003).

Example:
    >>> from vakr.adapters.icmp.transport import SocketIcmpProber
    >>> SocketIcmpProber().counters().sent
    0
"""
