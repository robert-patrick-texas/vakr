"""Self-health check of the ``vakr-api`` service, over its local socket (OPS-001, SVC-004).

``vakr check health`` and ``vakr doctor`` ask the running API, from the
outside, the same question a load balancer would: ``GET
/api/v1/health/ready`` on ``/run/vakr/api.sock``. The API never runs this
check itself, so it never depends on itself; its own ``health/detail``
reports its components directly.

- OK: not an installed node; no socket (the service is shipped disabled and
  is not running); or the API answers ``ready``. An answer of ``degraded``
  (telemetry unavailable) is OK here too, because the ``telemetry_db``
  check already reports that condition.
- CRITICAL: a socket nothing answers on (the service stopped uncleanly or
  hangs), no answer within ``TIMEOUT_S``, or ``not_ready`` (metadata
  database unavailable).
- UNKNOWN: the caller may not use the socket (run it as root or a member of
  ``vakr``), or an unexpected answer.

Dependencies:
    http.client, socket: An HTTP/1.1 request over a Unix socket, standard library only.
    vakr.api.settings: The socket path (no web framework is imported).

Safety:
    Read-only; sends no credentials (the socket authorizes by account).
    Summaries carry states and timings only.

Example:
    $ vakr check health
    VAKR OK - ...; api: ready in 4 ms | ... api_ready_s=0.004s
"""

import http.client
import json
import socket
import time

from vakr.api.settings import api_settings_from_config
from vakr.config.loader import ConfigError, EffectiveConfig, load_effective_config
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths

NAME = "api"
TIMEOUT_S = 2.0
READY_PATH = "/api/v1/health/ready"
MAX_ANSWER = 65536
"""Bytes of the readiness answer read at most."""


class UnixHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` to a Unix socket instead of a TCP port."""

    def __init__(self, path: str, timeout: float) -> None:
        """Prepare a connection.

        Args:
            path: The socket file.
            timeout: Seconds for connect and each read.

        Returns:
            None

        Raises:
            None
        """
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        """Connect to the socket.

        Args:
            None

        Returns:
            None

        Raises:
            OSError: If the socket refuses, is missing, or times out.
        """
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def probe(path: str, timeout: float = TIMEOUT_S) -> tuple[int, dict[str, object], float]:
    """Ask the API on its socket whether it is ready.

    Args:
        path: The socket file.
        timeout: Seconds to wait.

    Returns:
        tuple: HTTP status, the JSON answer (empty when not JSON), and seconds taken.

    Raises:
        OSError: If the socket cannot be reached or does not answer in time.
    """
    started = time.monotonic()
    connection = UnixHTTPConnection(path, timeout)
    try:
        connection.request("GET", READY_PATH, headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_ANSWER)
    finally:
        connection.close()
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    return response.status, body if isinstance(body, dict) else {}, time.monotonic() - started


def _socket_path(paths: InstallPaths) -> str:
    """Return the configured socket path, or the default when the configuration is unusable.

    Args:
        paths: Install layout.

    Returns:
        str: The socket path.

    Raises:
        None
    """
    try:
        config = load_effective_config(paths.config_dir, strict=False)
        return str(api_settings_from_config(config, paths).socket_path)
    except ConfigError, OSError, ValueError:
        return str(api_settings_from_config(EffectiveConfig({}), paths).socket_path)


def judge(status: int, body: dict[str, object], elapsed: float) -> CheckResult:
    """Turn the readiness answer into a check result.

    Args:
        status: HTTP status.
        body: The JSON answer.
        elapsed: Seconds taken.

    Returns:
        CheckResult: As the module docstring describes.

    Raises:
        None
    """
    perf = (PerfDatum("api_ready_s", round(elapsed, 4), "s"),)
    word = str(body.get("status", ""))
    if status == 200 and word in ("ready", "degraded"):
        note = " (telemetry unavailable)" if word == "degraded" else ""
        return CheckResult(NAME, Status.OK, f"ready{note} in {elapsed * 1000:.0f} ms", perf)
    if status == 503:
        return CheckResult(NAME, Status.CRITICAL, "not ready: the metadata database "
                           "is unavailable to vakr-api", perf)  # fmt: skip
    if status == 403:
        return CheckResult(NAME, Status.UNKNOWN, "this account may not use the API socket; run "
                           "as root or a member of vakr")  # fmt: skip
    return CheckResult(NAME, Status.UNKNOWN, f"unexpected answer from vakr-api (HTTP {status})")


def check_api() -> CheckResult:
    """Check the running API over its local socket.

    Args:
        None

    Returns:
        CheckResult: As the module docstring describes.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    if not paths.state_dir.exists():
        return CheckResult(NAME, Status.OK, f"no {paths.state_dir}; not an installed vakr node")
    path = _socket_path(paths)
    try:
        return judge(*probe(path))
    except FileNotFoundError:
        return CheckResult(NAME, Status.OK, f"vakr-api is not running (no {path})")
    except PermissionError:
        return CheckResult(NAME, Status.UNKNOWN, f"cannot reach {path}: permission denied; "
                           "run as root or a member of vakr")  # fmt: skip
    except TimeoutError:
        return CheckResult(NAME, Status.CRITICAL, f"vakr-api did not answer within {TIMEOUT_S:g} s")
    except (OSError, http.client.HTTPException) as exc:
        return CheckResult(NAME, Status.CRITICAL, f"vakr-api is not answering on {path} "
                           f"({type(exc).__name__}); see systemctl status vakr-api")  # fmt: skip
