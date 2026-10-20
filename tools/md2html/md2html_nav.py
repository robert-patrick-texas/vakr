# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Build md2html's left-hand page index and the top-bar repository link.

A documentation site that grows to dozens of pages needs a way to reach every
page from every page without going back to a listing. This module turns the
rendered pages into a directory tree and renders it, once per output file, as
a sidebar of nested ``<details>`` groups: root pages first, then each
directory in name order, with the groups that contain the current page open
and that page marked ``aria-current="page"``. Links are page-relative, so the
site works from any base path. It also renders the "GitHub" link for the top
bar. ``md2html.py`` places both into ``template.html``, whose CSS makes the
sidebar sticky on wide screens and a no-JavaScript toggle on phones. Like the
rest of md2html it imports nothing from the project that vendors it.

Dependencies:
    md2html_render: Provides the ``Page`` record whose path and title are
        listed.
    html, urllib.parse: Escape titles and percent-encode hrefs.

Safety considerations:
    Titles and directory names are HTML-escaped and hrefs percent-encoded, so
    a heading or file name cannot inject markup. Output depends only on the
    sorted page list, never on file-system order, so reruns are identical.

Example:
    >>> tree = build_tree([Page("README.md", title="Home"), Page("docs/a.md", title="A")])
    >>> 'aria-current="page">A</a>' in render_nav(tree, "docs/a.md", "../")
    True
"""

import html
import posixpath
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from md2html_render import Page

# A directory's landing pages, listed first in its group.
LANDING = ("README.md", "index.md")
# Octicons "mark-github" (MIT License, GitHub Inc.); no xmlns is needed inline.
GITHUB_MARK = (
    '<svg class="icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"'
    ' focusable="false"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29'
    " 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94"
    "-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87"
    ".87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08"
    "-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53"
    "-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75"
    "-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013"
    ' 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
)


@dataclass
class Folder:
    """One directory in the navigation tree, filled by ``build_tree``.

    Mutated only while the tree is built, then shared read-only by every
    page's ``render_nav`` call.

    Attributes:
        path (str): POSIX path relative to SRC_DIR ending in ``/``, or "" for
            the root.
        pages (list[Page]): Pages directly in this directory, landing pages
            first, then by case-folded file name.
        folders (dict[str, Folder]): Subdirectories keyed by name; rendered in
            sorted order.
    """

    path: str
    pages: list[Page] = field(default_factory=list)
    folders: dict[str, Folder] = field(default_factory=dict)


def page_order(page: Page) -> tuple[str, bool, str, str]:
    """Sort key: by directory, landing pages first, then by file name.

    File names rather than titles decide the order, so authors can sequence
    pages with prefixes such as ``01-``.

    Args:
        page (Page): Page to place.

    Returns:
        tuple[str, bool, str, str]: Directory, "not a landing page", the
        case-folded name and the exact name as a tie-breaker.

    Raises:
        None.
    """
    folder, name = posixpath.split(page.rel)
    return folder, name not in LANDING, name.casefold(), name


def build_tree(pages: list[Page]) -> Folder:
    """Arrange pages into a tree of directories.

    Args:
        pages (list[Page]): Rendered Markdown pages, in any order.

    Returns:
        Folder: The root directory.

    Raises:
        None.
    """
    root = Folder("")
    for page in sorted(pages, key=page_order):
        folder = root
        for part in page.rel.split("/")[:-1]:
            folder = folder.folders.setdefault(part, Folder(f"{folder.path}{part}/"))
        folder.pages.append(page)
    return root


def nav_link(page: Page, current: str, up: str) -> str:
    """Render one page as a list item, marking it when it is the current page.

    Args:
        page (Page): Page to link to.
        current (str): Source path of the page being rendered.
        up (str): ``../`` repeated to reach OUT_DIR from that page.

    Returns:
        str: An ``<li>`` holding a relative link labelled with the page title,
        or with the file name when the page has no H1.

    Raises:
        None.
    """
    # render_page falls back to the source path; the bare name reads better.
    title = posixpath.basename(page.rel) if page.title == page.rel else page.title
    mark = ' aria-current="page"' if page.rel == current else ""
    href = f"{up}{quote(page.rel[:-3])}.html"
    return f'<li><a href="{href}"{mark}>{html.escape(title)}</a></li>'


def render_folder(folder: Folder, current: str, up: str) -> str:
    """Render a directory's pages, then its subdirectories, as a nested list.

    Args:
        folder (Folder): Directory to render.
        current (str): Source path of the page being rendered.
        up (str): ``../`` repeated to reach OUT_DIR from that page.

    Returns:
        str: A ``<ul>`` element ending in a newline.

    Raises:
        None.
    """
    items = [nav_link(page, current, up) for page in folder.pages]
    for name in sorted(folder.folders):
        sub = folder.folders[name]
        only = sub.pages[0] if len(sub.pages) == 1 and not sub.folders else None
        if only and posixpath.basename(only.rel) in LANDING:
            # A directory holding just its README (a tool, say) is that page;
            # a group of one would cost a click and say nothing more.
            items.append(nav_link(only, current, up))
            continue
        state = " open" if current.startswith(sub.path) else ""
        items.append(
            f"<li><details{state}><summary>{html.escape(name)}/</summary>\n"
            f"{render_folder(sub, current, up)}</details></li>"
        )
    return "<ul>\n" + "\n".join(items) + "\n</ul>\n"


def render_nav(tree: Folder, current: str, up: str) -> str:
    """Render the sidebar listing every page for one output file.

    Args:
        tree (Folder): Root from ``build_tree``.
        current (str): Source path of the page being rendered; a path that
            is not a page (such as ``all-docs.html``) marks nothing.
        up (str): ``../`` repeated to reach OUT_DIR from that page.

    Returns:
        str: A ``<nav>`` element ending in a newline.

    Raises:
        None.
    """
    body = render_folder(tree, current, up)
    return f'<nav class="sidebar" id="sidebar" aria-label="All pages">\n{body}</nav>\n'


def repo_link(repo_url: str | None) -> str:
    """Render the top-bar link to the source repository.

    Args:
        repo_url (str | None): Value of ``--repo-url``, or None.

    Returns:
        str: A space and an ``<a>`` element labelled "GitHub" with the GitHub
        mark for GitHub hosts, or "Repository" elsewhere; "" without a URL.

    Raises:
        None.
    """
    url = (repo_url or "").rstrip("/")
    if not url:
        return ""
    if "github" in (urlsplit(url).hostname or ""):
        label, aria, icon = "GitHub", "GitHub repository", GITHUB_MARK
    else:
        label, aria, icon = "Repository", "Source repository", ""
    return f' <a class="repo" href="{html.escape(url)}" aria-label="{aria}">{icon}{label}</a>'
