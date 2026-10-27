"""``[node]`` edits for promote and demote: layer-aware, validated, backed up (CFGM-002, E.39).

Dependencies:
    pytest; vakr.config.node_edit.

Example:
    $ uv run pytest tests/unit/config/test_node_edit.py
"""

import tomllib
from pathlib import Path

import pytest

from vakr.config.node_edit import NodeEditError, plan_node_edit, write_node_edit
from vakr.replication.promotion import demoted, promoted

EAST = "https://vakr-east.example.net"
WEST = "https://vakr-west.example.net"


def _node(path: Path) -> dict:
    """Read one file's ``[node]`` table.

    Args:
        path: An operator file.

    Returns:
        dict: The table, or empty.

    Raises:
        None
    """
    return tomllib.loads(path.read_text()).get("node", {})


@pytest.fixture
def peer_in_dropin(tmp_path: Path) -> Path:
    """A peer whose role lives in a drop-in, as the system tests install it.

    Args:
        tmp_path: The configuration directory.

    Returns:
        Path: The configuration directory.

    Raises:
        None
    """
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "config.toml").write_text("config_version = 1\n# the operator's note\n")
    (tmp_path / "conf.d" / "40-position.toml").write_text('[node]\nposition = "west"\n')
    (tmp_path / "conf.d" / "50-role.toml").write_text(
        f'# keep this comment\n[node]\nrole = "peer"  # set by setup\nprimary_url = "{EAST}"\n'
    )
    return tmp_path


def test_cfgm002_promotion_edits_the_file_that_sets_the_role_and_backs_it_up(
    peer_in_dropin: Path, tmp_path: Path
) -> None:
    """CFGM-002, E.39: role and peer_url land where role is set; comments stay; a backup is kept.

    Args:
        peer_in_dropin: The configuration directory.
        tmp_path: Also holds the backup root.

    Returns:
        None

    Raises:
        None
    """
    from vakr.config.node import NodeSettings

    role_file = peer_in_dropin / "conf.d" / "50-role.toml"
    before = role_file.read_bytes()
    edit = plan_node_edit(peer_in_dropin, promoted(NodeSettings("peer", "west", EAST)))
    assert edit.after.role == "primary"
    assert edit.after.peer_url == EAST
    assert edit.files == [str(role_file)]
    backup = write_node_edit(peer_in_dropin, edit, tmp_path / "backups")
    assert backup is not None
    assert (backup / "conf.d" / "50-role.toml").read_bytes() == before
    text = role_file.read_text()
    assert "# keep this comment" in text
    assert "# set by setup" in text
    assert _node(role_file) == {"role": "primary", "peer_url": EAST}
    assert _node(peer_in_dropin / "conf.d" / "40-position.toml") == {"position": "west"}


def test_cfgm002_a_removed_key_goes_from_every_layer(tmp_path: Path) -> None:
    """CFGM-002: a key to remove is removed from each file that sets it, so none brings it back.

    Args:
        tmp_path: The configuration directory.

    Returns:
        None

    Raises:
        None
    """
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "config.toml").write_text(
        f'config_version = 1\n[node]\nrole = "peer"\nposition = "west"\nprimary_url = "{EAST}"\n'
    )
    (tmp_path / "conf.d" / "60-url.toml").write_text(f'[node]\nprimary_url = "{EAST}"\n')
    edit = plan_node_edit(tmp_path, {"role": "primary", "primary_url": None, "peer_url": EAST})
    write_node_edit(tmp_path, edit, tmp_path.parent / "b")
    assert _node(tmp_path / "config.toml") == {"role": "primary", "position": "west",
                                               "peer_url": EAST}  # fmt: skip
    assert "node" not in tomllib.loads((tmp_path / "conf.d" / "60-url.toml").read_text())


def test_cfgm003_an_invalid_result_writes_nothing(tmp_path: Path) -> None:
    """CFGM-003: a change that breaks the node rules, or an unknown key, is refused unwritten.

    Args:
        tmp_path: The configuration directory.

    Returns:
        None

    Raises:
        None
    """
    config = tmp_path / "config.toml"
    config.write_text(f'config_version = 1\n[node]\nrole = "primary"\npeer_url = "{WEST}"\n')
    before = config.read_bytes()
    with pytest.raises(NodeEditError, match=r"node\.position must be set on a peer"):
        plan_node_edit(tmp_path, demoted(WEST))
    with pytest.raises(NodeEditError, match="not a \\[node\\] setting"):
        plan_node_edit(tmp_path, {"colour": "red"})
    with pytest.raises(NodeEditError, match="primary_url"):
        plan_node_edit(tmp_path, {"role": "peer", "position": "east", "primary_url": "http://x"})
    assert config.read_bytes() == before


def test_cfgm002_a_missing_config_file_is_created(tmp_path: Path) -> None:
    """CFGM-002: with no operator file, the change goes into a new config.toml.

    Args:
        tmp_path: The configuration directory.

    Returns:
        None

    Raises:
        None
    """
    edit = plan_node_edit(tmp_path, {"position": "east"})
    assert write_node_edit(tmp_path, edit, tmp_path.parent / "b2") is not None
    assert _node(tmp_path / "config.toml") == {"position": "east"}
    assert write_node_edit(tmp_path, plan_node_edit(tmp_path, {"position": "east"}),
                           tmp_path.parent / "b2") is None  # fmt: skip
