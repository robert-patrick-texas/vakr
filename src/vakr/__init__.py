"""vakr: network performance and reachability monitoring for routers and switches.

vakr watches managed network devices with ICMP probe groups and SNMP polling,
keeping traffic, error, latency, and loss history that operators use in place
of MRTG and SmokePing. This top-level package deliberately does nothing at
import time; subpackages own collection, storage, presentation, and lifecycle
tooling behind the ports described in the project blueprint.

Dependencies:
    None at import time. See ``vakr.version`` for version lookup.

Safety:
    Importing ``vakr`` opens no sockets, files, or databases, so tools and
    tests can import it on any host without side effects.

Example:
    >>> import vakr.version
    >>> isinstance(vakr.version.get_version(), str)
    True
"""
