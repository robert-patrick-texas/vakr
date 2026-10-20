"""The SNMP simulator: a synthetic Cisco IOS XE dataset and two ways to serve it.

The dataset (``iosxe_dataset``) describes 80 routers and 20 switches whose
1,600 up/up interfaces match the MVP reference workload (blueprint 27.2).
It uses only RFC 5737 and RFC 3849 documentation addresses, locally
administered MACs, and ``example.net`` names. Communities are generated at
test time and never written to the repository.

Two servers read the same records (ADR-034):

- ``snmpsim_server`` runs the real ``snmpsim`` responder from ``.snmprec``
  files, so vakr's client is tested against an independent agent;
- ``agent`` is an in-process asyncio agent with fault injection (tooBig,
  timeouts, error statuses, garbage datagrams, restarts, and value changes)
  that snmpsim cannot script deterministically.

Dependencies:
    pysnmp, pyasn1: Message encoding for the in-process agent.
    snmpsim: The external responder (development dependency).

Example:
    $ uv run pytest tests/simulators/snmp
"""
