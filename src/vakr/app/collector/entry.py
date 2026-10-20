"""The collector process from start to exit: logging, preflight, signals, run (SVC-005, 16.3, 16.4).

``run_collector`` is what ``vakr collector run`` (and so
``vakr-collector.service``) executes. It installs journald-friendly logging
with secret redaction, runs the startup preflight, and either exits with
``EX_PREFLIGHT`` (78) and an operator sentence naming the failed check, or
runs the service with SIGTERM/SIGINT mapped to graceful stop and SIGHUP to
reload, and returns its exit status.

The unit does not restart on 78 (``RestartPreventExitStatus``): a bad
configuration or a schema mismatch needs an operator, and restarting would
only fill the journal (SVC-009, SVC-T05).

Dependencies:
    vakr.app.collector: Build and service.
    vakr.service: Logging, signals, sd_notify.

Safety:
    Logs never include secrets; the redaction filter is a backstop.

Example:
    >>> EX_PREFLIGHT
    78
"""

import logging

from vakr.app.collector.build import CollectorParts, build_parts
from vakr.app.collector.preflight import PreflightError
from vakr.app.collector.service import CollectorService
from vakr.app.collector.timings import EX_PREFLIGHT, ServiceTimings
from vakr.credentials import Secret
from vakr.paths import InstallPaths
from vakr.ports.snmp import SnmpSessionFactory
from vakr.service.logs import configure_service_logging
from vakr.service.sdnotify import notify
from vakr.service.signals import service_signals

_LOG = logging.getLogger(__name__)
__all__ = ["EX_PREFLIGHT", "run_collector"]


class _Secrets:
    """Secrets to redact from logs, once the provider exists.

    Attributes:
        parts: The collector parts, after the preflight.
    """

    def __init__(self) -> None:
        """Start with nothing to redact.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.parts: CollectorParts | None = None

    def __call__(self) -> tuple[Secret, ...]:
        """Return the loaded secrets.

        Args:
            None

        Returns:
            tuple[Secret, ...]: Every secret the provider holds now.

        Raises:
            None
        """
        return () if self.parts is None else self.parts.provider.known_secrets()


async def run_collector(
    paths: InstallPaths,
    *,
    development: bool = False,
    timings: ServiceTimings | None = None,
    snmp_factory: SnmpSessionFactory | None = None,
    level: int = logging.INFO,
) -> int:
    """Run the collector until it is stopped.

    Args:
        paths: The install layout.
        development: Tolerate unsafe credential-file permissions (workstations only).
        timings: Cadences; Appendix E.27 defaults in production.
        snmp_factory: SNMP adapter override, for tests.
        level: Lowest log level written.

    Returns:
        int: 0 after a clean stop, 1 after a component failure, 78 when the
        preflight refused to start.

    Raises:
        None
    """
    secrets = _Secrets()
    handler = configure_service_logging(secrets, level=level)
    try:
        try:
            secrets.parts = await build_parts(
                paths, development=development, snmp_factory=snmp_factory
            )
        except PreflightError as exc:
            _LOG.error(
                "startup check %s failed: %s",
                exc.check,
                exc.message,
                extra={"event_id": "collector.preflight_failed"},
            )
            notify(f"STATUS=startup check {exc.check} failed")
            return EX_PREFLIGHT
        for warning in secrets.parts.warnings:
            _LOG.warning("%s", warning, extra={"event_id": "collector.preflight_warning"})
        service = CollectorService(secrets.parts, timings)
        with service_signals(service.request_stop, service.request_reload):
            return await service.run()
    finally:
        logging.getLogger("vakr").removeHandler(handler)
