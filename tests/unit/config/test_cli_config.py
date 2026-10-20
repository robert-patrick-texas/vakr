"""``vakr config`` CLI, health check, and loader tests.

Requirements: CONF-006, CONF-009, CFGM-003, CFGM-004, OPS-001.

Every test relocates the installation with ``VAKR_ROOT`` so nothing touches
``/etc/vakr`` or ``/var/lib/vakr``.

Dependencies:
    pytest: Output capture, monkeypatching, temporary directories.

Example:
    $ uv run pytest tests/unit/config/test_cli_config.py
"""

import json
import secrets
import tomllib
from pathlib import Path

import pytest

from vakr.cli.main import main
from vakr.config import health, project
from vakr.config.loader import ConfigError, EffectiveConfig, load_effective_config
from vakr.health.model import Status
from vakr.lifecycle.configmerge import Catalog, LoadResult
from vakr.paths import InstallPaths
from vakr.version import get_version


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InstallPaths:
    """Relocate the installation under ``tmp_path`` with a starter config.toml.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        InstallPaths: The relocated layout.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.from_environment()
    (paths.config_dir / "conf.d").mkdir(parents=True)
    (paths.config_dir / "config.toml").write_text(project.template_text())
    return paths


def _dropin(paths: InstallPaths, text: str) -> Path:
    """Write ``conf.d/50-site.toml``.

    Args:
        paths: Installation layout.
        text: File content.

    Returns:
        Path: The drop-in.

    Raises:
        OSError: If writing fails.
    """
    path = paths.config_dir / "conf.d/50-site.toml"
    path.write_text(text)
    return path


def test_validate_and_show_effective(
    root: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """CONF-009 and CONF-006: validate exits 0/1 with file:line:key; show-effective names sources.

    Args:
        root: Relocated installation.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    throwaway = secrets.token_hex(8)  # generated per run; never a real credential
    proxy = f"http://ops:{throwaway}@proxy:3128"
    site = _dropin(root, f'[update]\nchannel = "stable"\n[network]\nhttps_proxy = "{proxy}"\n')
    assert main(["config", "validate"]) == 1
    err = capsys.readouterr().err
    assert f"{site}:4: network.https_proxy: must not contain credentials" in err
    assert throwaway not in err
    assert main(["config", "show-effective", "--sources"]) == 1
    out = capsys.readouterr().out
    assert f'update.channel = "stable"  # {site}:2' in out
    assert 'network.https_proxy = "http://***@proxy:3128"' in out
    assert throwaway not in out
    assert 'profile.name = "compact"  # default' in out
    _dropin(root, '[update]\nchannel = "stable"\n')
    assert main(["config", "validate", "--config-dir", str(root.config_dir)]) == 0
    assert "configuration valid: 2 operator file(s)" in capsys.readouterr().out
    assert main(["config", "show-effective"]) == 0
    assert tomllib.loads(capsys.readouterr().out)["update"]["channel"] == "stable"


def test_upgrade_records_release_and_refuses_invalid_config(
    root: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """CFGM-003: upgrade records the release; an invalid config exits 1 with files unchanged.

    Args:
        root: Relocated installation.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    site = _dropin(root, '# no version yet\n[update]\nchannel = "stable"\n')
    assert main(["config", "upgrade", "--dry-run"]) == 0
    assert "+config_version = 1" in capsys.readouterr().out
    assert not (root.state_dir / "config-state.json").exists()
    assert main(["config", "upgrade"]) == 0
    assert "upgraded 1 file(s)" in capsys.readouterr().out
    assert site.read_text().startswith("# no version yet\nconfig_version = 1\n")
    state = json.loads((root.state_dir / "config-state.json").read_text())
    assert (state["current_version"], state["previous_version"]) == (get_version(), None)
    assert main(["config", "upgrade"]) == 0
    assert "already at config_version 1" in capsys.readouterr().out
    before = site.read_bytes()
    site.write_text(before.decode() + "colour = 1\n")
    assert main(["config", "upgrade"]) == 1
    assert "config upgrade refused, operator files unchanged" in capsys.readouterr().err
    assert site.read_text().endswith("colour = 1\n")


def test_new_options_uses_previous_release_and_annotates(
    root: InstallPaths, capsys: pytest.CaptureFixture[str]
) -> None:
    """CFGM-T04 via the CLI: the baseline comes from the state file, and annotation is idempotent.

    Args:
        root: Relocated installation.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    assert main(["config", "new-options"]) == 0
    assert "no previous release recorded" in capsys.readouterr().err
    root.state_dir.mkdir(parents=True)
    state = root.state_dir / "config-state.json"
    state.write_text(json.dumps({"current_version": get_version(), "previous_version": "0.0.1"}))
    assert main(["config", "new-options"]) == 0
    assert f"options added since 0.0.1: {len(project.catalog().options)}" in capsys.readouterr().out
    assert main(["config", "new-options", "--since", get_version()]) == 0
    assert "options added since" in capsys.readouterr().out
    assert main(["config", "new-options", "--since", "nope"]) == 2
    assert main(["config", "new-options", "--annotate"]) == 0
    first = (root.config_dir / "config.toml").read_bytes()
    assert b"# New in " in first
    assert main(["config", "new-options", "--annotate"]) == 0
    assert "annotated 0 option(s)" in capsys.readouterr().out
    assert (root.config_dir / "config.toml").read_bytes() == first
    state.write_text("{not json")
    assert main(["config", "new-options"]) == 0
    _dropin(root, "[update]\nchanel = 1\n")
    state.write_text(json.dumps({"previous_version": "0.0.1"}))
    assert main(["config", "new-options", "--annotate"]) == 1


@pytest.mark.usefixtures("root")
def test_defaults_template_and_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``defaults`` renders the package set (CFGM-001), ``template`` the starter file (CFGM-005).

    Args:
        tmp_path: Temporary directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    out_dir = tmp_path / "opt/vakr/share/defaults"
    assert main(["config", "defaults", "--output", str(out_dir), "--markdown"]) == 0
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == [
        "config-migrations.toml",
        "config.toml.template",
        "configuration.md",
        "defaults.toml",
        "options.toml",
    ]
    assert (out_dir / "defaults.toml").read_text() == project.defaults_text()
    capsys.readouterr()
    assert main(["config", "defaults", "--markdown"]) == 0
    assert capsys.readouterr().out == project.reference_markdown()
    assert main(["config", "defaults"]) == 0
    assert capsys.readouterr().out == project.defaults_text()
    assert main(["config", "template"]) == 0
    assert capsys.readouterr().out == project.template_text()
    assert main(["config"]) == 2
    assert "SUBCOMMAND" in capsys.readouterr().err


def test_check_config_statuses(root: InstallPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    """OPS-001: CRITICAL when invalid, WARNING when an upgrade is pending, UNKNOWN when unreadable.

    Args:
        root: Relocated installation.
        monkeypatch: Simulates a pending upgrade and an unreadable directory.

    Returns:
        None

    Raises:
        None
    """
    assert health.check_config().status is Status.OK
    _dropin(root, "[update]\nchanel = 1\n")
    assert health.check_config().status is Status.CRITICAL
    newer = Catalog(2, project.catalog().options)
    monkeypatch.setattr(project, "catalog", lambda: newer)
    monkeypatch.setattr(project, "inspect", lambda _d: LoadResult(versions={"config.toml": 1}))
    result = health.check_config()
    assert (result.status, "config_version 1 < 2" in result.summary) == (Status.WARNING, True)

    def unreadable(_config_dir: Path) -> LoadResult:
        """Simulate a permission error.

        Args:
            _config_dir: Ignored.

        Returns:
            LoadResult: Never returns.

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(13, "denied")

    monkeypatch.setattr(project, "inspect", unreadable)
    assert health.check_config().status is Status.UNKNOWN


def test_loader_raises_config_error_and_tracks_sources(root: InstallPaths) -> None:
    """``load_effective_config`` validates (ConfigError is a ValueError) and reports sources.

    Args:
        root: Relocated installation.

    Returns:
        None

    Raises:
        None
    """
    site = _dropin(root, '[update]\nchannel = "stable"\n')
    cfg = load_effective_config(root.config_dir)
    assert cfg.source("update.channel") == f"{site}:2"
    assert cfg.source("profile.name") == "default"
    assert cfg.source("missing.key") == ""
    keywords = cfg.get("interface_classification.keywords")
    keywords.clear()
    assert len(cfg.get("interface_classification.keywords")) == 3
    _dropin(root, "[update]\nchanel = 1\n")
    with pytest.raises(ValueError, match=r"update\.chanel: unknown key") as caught:
        load_effective_config(root.config_dir)
    assert isinstance(caught.value, ConfigError)
    assert len(caught.value.issues) == 1
    assert EffectiveConfig({"a": [1]}).get("a.b", "d") == "d"
