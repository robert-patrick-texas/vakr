"""The resilience system-test harness, MVP-ACC-06 (blueprint 27.6, Appendix E.36).

``run.sh`` starts one systemd node and a simulated fleet in containers and
puts the node through a service restart, a host reboot, the telemetry store
unavailable, and an abrupt power loss. ``probe.py`` and ``faults.py`` run
inside the node with the packaged interpreter; ``accounting.py`` decides on
the host whether any expected poll was silently lost. See
``docs/development/resilience-tests.md``.

Dependencies:
    None at package level; see each module.

Example:
    $ make resilience-test DEB=dist/vakr_1.4.0_amd64.deb
"""
