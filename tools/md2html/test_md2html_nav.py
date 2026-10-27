# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Tests for md2html's sidebar page index and top-bar repository link.

Every published page must link back to the repository and carry a left-hand
index of all pages, grouped by directory, with the current page marked, that
collapses behind a no-JavaScript toggle on phones (DOCPUB-004). The index is
repeated on every page, so it must use relative links that work from nested
directories and under any base path, and it must stay byte-for-byte
deterministic. Each test builds a throwaway tree under ``tmp_path``.

Dependencies:
    pytest: Test runner; ``tmp_path`` isolates the source and output trees.
    md2html, md2html_nav: Modules under test; pytest adds this directory to
        ``sys.path``.

Safety considerations:
    Tests only write beneath ``tmp_path`` and never access the network.

Example:
    $ uv run --with pytest==9.2.0 --with markdown-it-py==4.2.0 \
        --with mdit-py-plugins==0.6.1 pytest tools/md2html
"""

import posixpath
import re
from pathlib import Path

import md2html
from md2html_nav import repo_link

REPO = ["--repo-url", "https://github.com/o/r/", "--branch", "main"]
FILES = {
    "README.md": "# Home\n",
    "CHANGELOG.md": "# Changes & `<notes>`\n",
    "about.md": "# About\n",
    "docs/README.md": "# Docs\n",
    "docs/b.md": "# Bravo\n",
    "docs/A.md": "# Alpha\n",
    "docs/sub/deep.md": "# Deep\n",
    "docs/sub/plain.md": "No heading, so the file name is the title.\n",
    "tools/one/README.md": "# One tool\n",
    "tools/two/README.md": "# Two tool\n",
    "tools/two/notes.md": "# Two notes\n",
}
PAGES = {rel[:-3] + ".html" for rel in FILES}
NAV = re.compile(r'<nav class="sidebar" id="sidebar" aria-label="All pages">\n.*?</nav>\n', re.S)
LABEL = re.compile(r"<summary>([^<]*)</summary>|<a href=\"[^\"]*\"[^>]*>([^<]*)</a>")


def site(tmp_path: Path, files: dict[str, str], *flags: str) -> dict[str, str]:
    """Build a site from ``files`` and return every HTML page it wrote.

    Args:
        tmp_path (Path): Directory that receives ``src/`` and ``out/``.
        files (dict[str, str]): POSIX relative path mapped to Markdown text.
        *flags (str): Extra command-line options such as ``--repo-url``.

    Returns:
        dict[str, str]: Output path relative to ``out/`` mapped to its HTML.

    Raises:
        OSError: If the tree cannot be written or the site cannot be built.
        AssertionError: If md2html reports failure.
    """
    src, out = tmp_path / "src", tmp_path / "out"
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text, encoding="utf-8")
    assert md2html.main([str(src), str(out), *flags]) == 0
    return {p.relative_to(out).as_posix(): p.read_text("utf-8") for p in out.rglob("*.html")}


def nav_of(page: str) -> str:
    """Return the sidebar element of one rendered page.

    Args:
        page (str): Complete HTML document.

    Returns:
        str: The ``<nav class="sidebar">`` element.

    Raises:
        AssertionError: If the page has no sidebar or more than one.
    """
    found = NAV.findall(page)
    assert len(found) == 1
    return found[0]


def test_every_page_links_to_the_repository(tmp_path: Path) -> None:
    """The top bar links to the repository with an inline mark (DOCPUB-004).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If any page lacks the accessible GitHub link.
    """
    pages = site(tmp_path, FILES, *REPO)
    link = '<a class="repo" href="https://github.com/o/r" aria-label="GitHub repository"><svg'
    for name, page in pages.items():
        header = page.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
        assert link in header, name
        assert 'aria-hidden="true" focusable="false"><path' in header
        assert "</svg>GitHub</a></nav>" in header
    assert repo_link(None) == repo_link("") == ""
    other = repo_link("https://git.example.org/team/x/?a=1&b=2")
    assert other == (
        ' <a class="repo" href="https://git.example.org/team/x/?a=1&amp;b=2"'
        ' aria-label="Source repository">Repository</a>'
    )
    assert 'class="repo"' not in site(tmp_path / "bare", FILES)["index.html"]


def test_sidebar_lists_every_page_with_relative_links(tmp_path: Path) -> None:
    """Every page's index links to all pages relatively, at any depth (DOCPUB-004).

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If a page is missing, a link is absolute, or a link
            does not resolve to a written page.
    """
    pages = site(tmp_path, FILES, *REPO)
    assert {"index.html", "all-docs.html", "docs/index.html"} <= set(pages)
    for name, page in pages.items():
        hrefs = re.findall(r'href="([^"]*)"', nav_of(page))
        assert all(not h.startswith("/") and "://" not in h for h in hrefs), name
        targets = {posixpath.normpath(posixpath.join(posixpath.dirname(name), h)) for h in hrefs}
        assert targets == PAGES, name
        assert len(hrefs) == len(PAGES), name
    deep = nav_of(pages["docs/sub/deep.html"])
    assert '<a href="../../README.html">Home</a>' in deep
    assert '<a href="../../docs/sub/plain.html">plain.md</a>' in deep


def test_current_page_is_marked_and_its_groups_open(tmp_path: Path) -> None:
    """Only the current page has ``aria-current`` and only its groups are open.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If marking or group state is wrong (DOCPUB-004).
    """
    pages = site(tmp_path, FILES)
    expected = {name: name for name in PAGES} | {
        "index.html": "README.html",
        "docs/index.html": "docs/README.html",
        "tools/one/index.html": "tools/one/README.html",
        "tools/two/index.html": "tools/two/README.html",
    }
    for name, page in pages.items():
        current = re.findall(r'<a href="([^"]*)" aria-current="page">', nav_of(page))
        resolved = [posixpath.normpath(posixpath.join(posixpath.dirname(name), h)) for h in current]
        assert resolved == ([expected[name]] if name in expected else []), name
    deep = nav_of(pages["docs/sub/deep.html"])
    opened = re.findall(r"<details( open)?><summary>([^<]*)</summary>", deep)
    assert opened == [(" open", "docs/"), (" open", "sub/"), ("", "tools/"), ("", "two/")]
    assert "<details open>" not in nav_of(pages["all-docs.html"])
    assert "<details open><summary>tools/" in nav_of(pages["tools/one/README.html"])


def test_groups_follow_directories_in_sorted_order(tmp_path: Path) -> None:
    """Root pages come first, then directories; landing pages lead each group.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If the order, labels or escaping differ.
    """
    nav = nav_of(site(tmp_path, FILES)["about.html"])
    labels = [summary or link for summary, link in LABEL.findall(nav)]
    assert labels == [
        "Home",
        "About",
        "Changes &amp; &lt;notes&gt;",
        "docs/",
        "Docs",
        "Alpha",
        "Bravo",
        "sub/",
        "Deep",
        "plain.md",
        "tools/",
        "One tool",
        "two/",
        "Two tool",
        "Two notes",
    ]
    # A directory holding only its README is listed as that page, not a group.
    assert "<summary>one/</summary>" not in nav


def test_nav_output_is_deterministic(tmp_path: Path) -> None:
    """Builds from the same files in any creation order are byte-identical.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If two builds differ.
    """
    first = site(tmp_path / "a", FILES, *REPO)
    second = site(tmp_path / "b", dict(reversed(FILES.items())), *REPO)
    assert first == second


def test_phone_toggle_needs_no_javascript(tmp_path: Path) -> None:
    """The index collapses behind a CSS checkbox toggle and keeps both themes.

    Args:
        tmp_path (Path): Per-test temporary directory from pytest.

    Returns:
        None.

    Raises:
        AssertionError: If the toggle, layout rules or themes are missing.
    """
    page = site(tmp_path, FILES)["docs/b.html"]
    assert "<script" not in page
    body = page.split("<body>", 1)[1]
    assert body.startswith('\n<input type="checkbox" id="nav-toggle" class="nav-toggle"')
    assert '<label class="nav-label" for="nav-toggle">' in body
    assert body.index("nav-toggle") < body.index("<header") < body.index('class="sidebar"')
    for rule in [
        ".nav-toggle:checked ~ .layout .sidebar { display: block; }",
        "@media (min-width: 60rem)",
        "position: sticky",
        "overflow-y: auto",
        ".content { min-width: 0; }",
        "@media (prefers-color-scheme: dark)",
        '.sidebar a[aria-current="page"]',
    ]:
        assert rule in page
    assert page.count("--current:") == 2
    assert '<a href="../all-docs.html">All docs</a>' in page
