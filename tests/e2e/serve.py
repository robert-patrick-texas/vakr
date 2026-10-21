"""Run a real ``vakr api serve`` on a freshly seeded install for Playwright (WEB-T01 to WEB-T11).

``python -m tests.e2e.serve --port PORT --web-dir DIR --fixtures-out FILE
[--scenario normal|no-telemetry]``:

1. builds a temporary install (``tests.e2e.seed``) with the fleet, the
   accounts ``e2e-admin`` and ``e2e-operator``, and the display zone
   ``America/Chicago``;
2. writes ``FILE``: the scenario, the seed's anchor time, and UUIDs by
   name (devices, ``device/ifName`` interfaces, collectors, sites, tags,
   groups, open reviews); no secret is in it;
3. starts ``vakr api serve`` exactly as the unit does, with
   ``VAKR_ROOT`` set to the install and ``VAKR_WEB_DIR`` to the web build,
   and waits until ``/api/v1/health/live`` answers;
4. writes one fresh poll a minute for every polled device (the "fixture
   collector"), so freshness never decays during a long run, until SIGTERM
   or SIGINT; then it stops the server and deletes the install.

Passwords come from ``VAKR_E2E_ADMIN_PASSWORD`` and
``VAKR_E2E_OPERATOR_PASSWORD``; the Playwright config generates them per
run. Unset, random ones are made, and are never printed.

Dependencies:
    subprocess, signal, urllib: The server process and its readiness.
    tests.e2e.seed: The install.

Safety:
    Loopback only; the install lives in a fresh temporary directory removed
    at exit. Passwords are never logged, written, or put on a command line.

Example:
    $ uv run python -m tests.e2e.serve --port 18990 --web-dir web/build --fixtures-out fx.json
"""

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from tests.e2e.seed import ADMIN_USER, OPERATOR_USER, SCENARIOS, Install, build_install

PASSWORD_ENV = {ADMIN_USER: "VAKR_E2E_ADMIN_PASSWORD", OPERATOR_USER: "VAKR_E2E_OPERATOR_PASSWORD"}
TICK_S = 20.0
READY_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 10.0
_SOCKET_MAX = 100
"""``sun_path`` holds 108 bytes; stay below it for ``run/vakr/api.sock``."""


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments; ``None`` means ``sys.argv[1:]``.

    Returns:
        argparse.Namespace: ``port``, ``web_dir``, ``fixtures_out``, ``scenario``.

    Raises:
        SystemExit: On a usage error.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tests.e2e.serve", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--port", type=int, required=True, help="loopback TCP port of the API")
    parser.add_argument("--web-dir", type=Path, required=True, help="the web build (web/build)")
    parser.add_argument("--fixtures-out", type=Path, required=True, help="where to write UUIDs")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    return parser.parse_args(argv)


def passwords_from(environ: dict[str, str]) -> dict[str, str]:
    """Read the accounts' passwords from the environment, inventing missing ones.

    Args:
        environ: The environment.

    Returns:
        dict[str, str]: Password by username.

    Raises:
        None
    """
    return {
        user: environ.get(var) or secrets.token_urlsafe(24) for user, var in PASSWORD_ENV.items()
    }


def temp_root() -> Path:
    """Make the install directory, short enough for the API's Unix socket path.

    Args:
        None

    Returns:
        Path: A new empty directory.

    Raises:
        None
    """
    root = Path(tempfile.mkdtemp(prefix="vakr-e2e-"))
    if len(str(root / "run/vakr/api.sock")) > _SOCKET_MAX:
        root.rmdir()
        root = Path(tempfile.mkdtemp(prefix="vakr-e2e-", dir="/tmp"))
    return root


def start_server(install: Install, web_dir: Path, port: int) -> subprocess.Popen[bytes]:
    """Start ``vakr api serve`` on the install and wait until it answers.

    Args:
        install: The seeded install.
        web_dir: The web build.
        port: Its TCP port.

    Returns:
        subprocess.Popen[bytes]: The running server.

    Raises:
        RuntimeError: If it exits or does not answer within ``READY_TIMEOUT_S``.
    """
    env = {k: v for k, v in os.environ.items() if k not in PASSWORD_ENV.values()}
    env |= {"VAKR_ROOT": str(install.paths.root), "VAKR_WEB_DIR": str(web_dir.resolve())}
    child = subprocess.Popen([sys.executable, "-m", "vakr.cli.main", "api", "serve"], env=env)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"vakr api serve exited with {child.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health/live", timeout=2):
                return child
        except OSError:
            time.sleep(0.2)
    child.terminate()
    raise RuntimeError(f"vakr api serve did not answer on port {port}")


def run_until_stopped(install: Install, child: subprocess.Popen[bytes]) -> int:
    """Feed fresh polls until a signal arrives or the server exits.

    Args:
        install: The seeded install.
        child: The server.

    Returns:
        int: 0 after a signal, 1 when the server died on its own.

    Raises:
        None
    """
    stop = threading.Event()

    def on_signal(_signum: int, _frame: FrameType | None) -> None:
        """Ask the loop to stop.

        Args:
            _signum: The signal.
            _frame: The interrupted frame.

        Returns:
            None

        Raises:
            None
        """
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    while not stop.wait(TICK_S):
        if child.poll() is not None:
            print(f"e2e-serve: vakr api serve exited with {child.returncode}", file=sys.stderr)
            return 1
        if install.collector is not None:
            install.collector.tick(datetime.now(UTC))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Seed, serve, refresh, and clean up.

    Args:
        argv: Command-line arguments.

    Returns:
        int: Exit status.

    Raises:
        None
    """
    args = parse(argv)
    root = temp_root()
    child: subprocess.Popen[bytes] | None = None
    install: Install | None = None
    try:
        install = build_install(root, args.port, args.scenario, passwords_from(dict(os.environ)))
        fixtures = install.seeded.fixtures(args.scenario, install.anchor)
        args.fixtures_out.parent.mkdir(parents=True, exist_ok=True)
        args.fixtures_out.write_text(json.dumps(fixtures, indent=2, sort_keys=True) + "\n")
        child = start_server(install, args.web_dir, args.port)
        print(f"e2e-serve: ready on http://127.0.0.1:{args.port} ({args.scenario})", flush=True)
        return run_until_stopped(install, child)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                child.kill()
        if install is not None and install.collector is not None:
            install.collector.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
