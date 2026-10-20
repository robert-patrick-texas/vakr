"""`vakr config new-options` is short by default and full with --describe (CFGM-004).

Self-update prints this listing after an update, so a release that adds
dozens of options must not flood the update log. The header line stays the
same in both forms, because updaters from earlier releases parse it.

Dependencies:
    pytest; the packaged catalog.

Example:
    $ uv run pytest tests/unit/config/test_option_listing.py
"""

import pytest

from vakr.cli.main import main
from vakr.config import project
from vakr.config.option_listing import WIDTH, brief_lines, described_lines


def test_brief_listing_groups_names_by_section_within_the_width() -> None:
    """CFGM-004: one wrapped line per section, then where the descriptions are.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    options = project.catalog().options
    lines = brief_lines(options, "0.0.1")
    sections = {o.key.split(".", 1)[0] for o in options}
    starts = [ln for ln in lines if not ln.startswith("      ")]
    assert len(starts) == len(sections) + 1  # one line per section, plus the hint
    assert lines[-1] == "  descriptions: vakr config new-options --since 0.0.1 --describe"
    assert all(len(ln) <= WIDTH for ln in lines)
    assert len(lines) < len(described_lines(options)) / 3
    named = " ".join(lines)
    assert all(o.key.split(".", 1)[-1] in named for o in options)


def test_cli_header_is_unchanged_and_describe_adds_descriptions(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Older updaters parse ``options added since X: N``; --describe restores the full text.

    Args:
        capsys: Output capture.
        monkeypatch: Relocates the install.
        tmp_path: Install root.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    total = len(project.catalog().options)
    assert main(["config", "new-options", "--since", "0.0.1"]) == 0
    brief = capsys.readouterr().out.splitlines()
    assert brief[0] == f"options added since 0.0.1: {total}"
    assert main(["config", "new-options", "--since", "0.0.1", "--describe"]) == 0
    full = capsys.readouterr().out.splitlines()
    assert full[0] == brief[0]
    assert any("reload restart; since" in ln for ln in full)
    assert not any("reload restart; since" in ln for ln in brief)
