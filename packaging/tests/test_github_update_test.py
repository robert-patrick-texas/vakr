# ruff: noqa: S101 - pytest assertions (the repo's tests/** ignore does not reach here)
"""The post-release GitHub update check is runnable and self-contained (UPD-T01).

``make github-update-test`` runs only after a release is published and needs
Docker and the network, so CI cannot run it; these checks keep the scripts
from rotting between releases.

Dependencies:
    pytest; bash for syntax checks.

Example:
    $ uv run pytest packaging/tests/test_github_update_test.py
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
SCRIPTS = (PKG / "github-update-test.sh", PKG / "container" / "github-update-inner.sh")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_and_are_executable(script: Path) -> None:
    """Both halves are valid bash and executable.

    Args:
        script: One of the two scripts.

    Returns:
        None

    Raises:
        None
    """
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)  # noqa: S603, S607


def test_checks_are_generic_and_use_the_shared_helpers() -> None:
    """No release number or key is hard-coded; the inner half uses testlib's helpers.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    host, inner = (p.read_text(encoding="utf-8") for p in SCRIPTS)
    assert "packaging/release-signers" in host  # the trusted key comes from this checkout
    assert "SHA256:" not in host + inner
    assert ". /test/testlib.sh" in inner
    assert inner.rstrip().endswith("finish github-update-test")
    for needed in ("FROM", "TO", "REPO", "EXPECTED_FP"):
        assert f"-e {needed}=" in host
