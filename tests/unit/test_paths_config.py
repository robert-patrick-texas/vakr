"""Unit tests for install paths and the effective-config interface.

Dependencies:
    pytest: Temporary directories.

Example:
    $ uv run pytest tests/unit/test_paths_config.py
"""

from pathlib import Path

from vakr.config.loader import EffectiveConfig, load_effective_config
from vakr.paths import InstallPaths


def test_paths_relocate_under_vakr_root() -> None:
    """``VAKR_ROOT`` moves the whole layout, and ``/opt/vakr`` holds the payload.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    prod = InstallPaths.from_environment({})
    assert prod.release_signers == Path("/opt/vakr/share/release-signers")
    assert prod.ca_dir == Path("/etc/vakr/tls/ca.d")
    test = InstallPaths.from_environment({"VAKR_ROOT": "/srv/x"})
    assert test.state_dir == Path("/srv/x/var/lib/vakr")
    assert test.defaults_dir == Path("/srv/x/opt/vakr/share/defaults")


def test_conf_d_overrides_base_and_merges_tables(tmp_path: Path) -> None:
    """``conf.d`` files override ``config.toml`` key by key, in lexical order.

    Args:
        tmp_path: Temporary configuration directory.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "config.toml").write_text('[update]\nchannel = "stable"\nrepository = "a/b"\n')
    (tmp_path / "conf.d/20-z.toml").write_text('[update]\nchannel = "prerelease"\n')
    (tmp_path / "conf.d/10-a.toml").write_text('[update]\nchannel = "ignored"\n')
    cfg = load_effective_config(tmp_path)
    assert cfg.get("update.channel") == "prerelease"
    assert cfg.get("update.repository") == "a/b"
    assert cfg.get("update.missing", "d") == "d"
    assert cfg.get("update.channel.deeper") is None


def test_missing_directory_is_empty_and_copies_are_independent(tmp_path: Path) -> None:
    """A fresh install has no files yet and loads packaged defaults; ``as_dict`` is a copy.

    Before CFGM-001 an empty directory loaded as ``{}``; it now yields every
    catalog default, so the expectation checks the ``[update]`` defaults.

    Args:
        tmp_path: Empty directory.

    Returns:
        None

    Raises:
        None
    """
    assert load_effective_config(tmp_path / "absent").as_dict()["update"] == {
        "repository": "robert-patrick-texas/vakr",
        "channel": "prerelease",
        "hold_version": "",
    }
    cfg = EffectiveConfig({"a": {"b": 1}})
    copy = cfg.as_dict()
    copy["a"]["b"] = 2
    assert cfg.get("a.b") == 1
