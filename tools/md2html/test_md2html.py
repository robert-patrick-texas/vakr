# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Tests for md2html, the portable Markdown-to-HTML site builder.

Published docs are only trustworthy if table-of-contents anchors resolve as
on GitHub and the link check fails the Pages build on a broken file link or
anchor (DOCPUB-001, DOCPUB-002, DOCPUB-003, QG-009). Each test builds a
throwaway tree under ``tmp_path``, so nothing touches the repository.

Dependencies:
    pytest: Test runner; ``tmp_path`` and ``capsys`` isolate files and output.
    md2html, md2html_render: Modules under test; pytest adds this directory
        to ``sys.path``.

Safety considerations:
    Tests only write beneath ``tmp_path`` and never access the network.

Example:
    $ uv run --with pytest==9.1.1 --with markdown-it-py==4.2.0 \\
        --with mdit-py-plugins==0.6.1 pytest tools/md2html
"""

from pathlib import Path

import md2html
import pytest
from md2html_render import github_slug, make_parser, render_page


def make_tree(root: Path, files: dict[str, str]) -> Path:
    """Create text files below ``root`` from a path-to-content mapping.

    Args:
        root (Path): Directory that receives the files.
        files (dict[str, str]): POSIX relative path mapped to UTF-8 content.

    Returns:
        Path: ``root``, for chaining.

    Raises:
        OSError: If a file cannot be written.
    """
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def build(tmp_path: Path, files: dict[str, str], *flags: str) -> tuple[int, Path]:
    """Build a site from ``files`` with md2html and return its exit status.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        files (dict[str, str]): Source tree to create under ``tmp_path/src``.
        *flags (str): Extra command-line options such as ``--check``.

    Returns:
        tuple[int, Path]: Exit status and the output directory.

    Raises:
        OSError: If the tree cannot be written or the site cannot be built.
    """
    src, out = make_tree(tmp_path / "src", files), tmp_path / "out"
    return md2html.main([str(src), str(out), *flags]), out


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        (
            "1. Purpose and instructions to the implementation agent",
            "1-purpose-and-instructions-to-the-implementation-agent",
        ),
        (
            "22. Previously overlooked or ambiguous areas and their resolution",
            "22-previously-overlooked-or-ambiguous-areas-and-their-resolution",
        ),
        ("5.2 Mandatory persistence ports", "52-mandatory-persistence-ports"),
        ("“TimeseriesDB” interpretation", "timeseriesdb-interpretation"),
        ("17.6 “What and why” comments", "176-what-and-why-comments"),
        ("What's new? (v2) — notes!", "whats-new-v2--notes"),
        ("snake_case and kebab-case", "snake_case-and-kebab-case"),
        ("Ünïcode Straße 2", "ünïcode-straße-2"),
    ],
)
def test_slug_matches_github(heading: str, slug: str) -> None:
    """Heading slugs follow GitHub's rules, including punctuation (DOCPUB-002).

    Args:
        heading (str): Plain heading text.
        slug (str): Anchor GitHub generates for that heading.

    Returns:
        None.

    Raises:
        AssertionError: If the slug differs from GitHub's.
    """
    assert github_slug(heading) == slug


def test_duplicate_headings_get_numbered_suffixes(tmp_path: Path) -> None:
    """Repeated headings get -1, -2 suffixes and a "¶" self-link (DOCPUB-002).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If ids or self-links are wrong.
    """
    make_tree(tmp_path, {"a.md": "# Setup\n\n## Setup\n\n### Setup\n\n## Setup 1\n"})
    page = render_page(make_parser(), "a.md", tmp_path, None)
    assert {"setup", "setup-1", "setup-2", "setup-1-1"} <= page.anchors
    anchor = '<h3 id="setup-2">Setup <a class="header-anchor" href="#setup-2">¶</a>'
    assert anchor in page.body
    assert page.title == "Setup"


def test_relative_md_links_become_html(tmp_path: Path) -> None:
    """Relative ``.md`` links become ``.html``; other links are untouched.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If any href is rewritten incorrectly.
    """
    cases = {  # Markdown target -> href expected in the HTML
        "other.md": "other.html",
        "sub/page.md#part-two": "sub/page.html#part-two",
        "https://example.com/x.md": "https://example.com/x.md",
        "mailto:ops@example.com": "mailto:ops@example.com",
        "#local": "#local",
        "../up.md": "../up.html",
    }
    text = "# A\n\n" + "\n".join(f"[x]({target})" for target in cases) + "\n"
    make_tree(tmp_path, {"docs/a.md": text})
    body = render_page(make_parser(), "docs/a.md", tmp_path, None).body
    for href in cases.values():
        assert f'href="{href}"' in body


def test_readme_renders_as_index_unless_index_md_exists(tmp_path: Path) -> None:
    """README.md also becomes index.html, except beside an index.md (DOCPUB-001).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If the index pages or the listing are wrong.
    """
    files = {
        "README.md": "# Root readme\n",
        "docs/README.md": "# Docs readme\n",
        "guide/README.md": "# Guide readme\n",
        "guide/index.md": "# Guide index\n",
    }
    status, out = build(tmp_path, files)
    assert status == 0
    for name, title in [
        ("index.html", "Root readme"),
        ("README.html", "Root readme"),
        ("docs/index.html", "Docs readme"),
        ("docs/README.html", "Docs readme"),
        ("guide/index.html", "Guide index"),
        ("guide/README.html", "Guide readme"),
    ]:
        assert f"<title>{title}</title>" in (out / name).read_text(encoding="utf-8")
    listing = (out / "all-docs.html").read_text(encoding="utf-8")
    assert "<h2>guide/</h2>" in listing
    assert '<a href="guide/index.html">Guide index</a>' in listing


def test_check_reports_broken_file_link_and_anchor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--check`` prints file:line problems and exits 1 (DOCPUB-002, QG-009).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        capsys (pytest.CaptureFixture[str]): Captures the printed report.

    Returns:
        None.

    Raises:
        AssertionError: If a broken link is missed or a valid one reported.
    """
    files = {
        "a.md": "# A\n\n[ok](b.md#real-part)\n[gone](missing.md)\n\n"
        "| x |\n|---|\n| [bad](b.md#nope) |\n\n[self](#a) [lost](#nowhere)\n",
        "b.md": "# Real part\n",
    }
    status, _ = build(tmp_path, files, "--check")
    src = tmp_path / "src"
    assert status == 1
    assert capsys.readouterr().out.splitlines() == [
        f"{src / 'a.md'}:4: broken link -> missing.md",
        f"{src / 'a.md'}:8: broken link -> b.md#nope",
        f"{src / 'a.md'}:10: broken link -> #nowhere",
    ]


def test_check_passes_on_valid_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A tree whose links all resolve passes ``--check`` silently (DOCPUB-002).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.
        capsys (pytest.CaptureFixture[str]): Captures the printed report.

    Returns:
        None.

    Raises:
        AssertionError: If a valid link is reported as broken.
    """
    files = {
        "README.md": "# Home\n\n[docs](docs/) [lic](LICENSE) [x](docs/x.md#x-y)\n",
        "LICENSE": "Apache-2.0\n",
        "docs/README.md": "# Docs\n\n[home](../README.md#home) [up](../)\n",
        "docs/x.md": "# X: y\n\nNote.[^1]\n\n[^1]: [Back](#x-y).\n",
    }
    status, _ = build(tmp_path, files, "--check")
    assert status == 0
    assert capsys.readouterr().out == ""


def test_images_exclusions_and_deterministic_output(tmp_path: Path) -> None:
    """Images are copied, excluded trees skipped, and reruns byte-identical.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If outputs differ or exclusions are ignored.
    """
    files = {
        "a.md": "# A\n\n![logo](img/logo.svg)\n",
        "img/logo.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>\n",
        "drafts/wip.md": "# WIP\n",
        "node_modules/pkg/README.md": "# Vendored\n",
    }
    _, out = build(tmp_path, files, "--exclude", "drafts")
    first = {p: p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}
    _, out = build(tmp_path, files, "--exclude", "drafts")
    assert first == {p: p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}
    assert (out / "img/logo.svg").read_bytes() == files["img/logo.svg"].encode()
    assert not (out / "drafts").exists()
    assert not (out / "node_modules").exists()


def test_page_template_and_github_features(tmp_path: Path) -> None:
    """Pages are self-contained and render GitHub-style syntax (DOCPUB-003).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If template parts or Markdown features are missing.
    """
    (tmp_path / ".git").mkdir()
    text = "# Ops *guide*\n\n|a|b|\n|-|-|\n|1|2|\n\n- [ ] todo\n\n~~old~~\n\n```toml\nk = 1\n```\n"
    files = {"docs/sub/g.md": text, "docs/README.md": "# Docs\n"}
    make_tree(tmp_path, files)
    repo = ["--repo-url", "https://github.com/o/r/", "--branch", "dev"]
    md2html.main([str(tmp_path / "docs"), str(tmp_path / "out"), *repo])
    page = (tmp_path / "out/sub/g.html").read_text(encoding="utf-8")
    blob = "https://github.com/o/r/blob/dev/docs/sub/g.md"
    for part in [
        "<title>Ops guide</title>",
        '<a href="../index.html">Home</a>',
        '<a href="../all-docs.html">All docs</a>',
        '<div class="table-wrap">\n<table>',
        'class="task-list-item-checkbox" disabled="disabled" type="checkbox"',
        "<s>old</s>",
        '<code class="language-toml">',
        "prefers-color-scheme: dark",
        f'Source: <a href="{blob}">docs/sub/g.md</a>',
    ]:
        assert part in page
    assert "http://" not in page
    assert "//cdn" not in page
