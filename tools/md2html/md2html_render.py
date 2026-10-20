# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Render one Markdown file to HTML and resolve the local references in it.

This is the page-level half of md2html; ``md2html.py`` assembles the site. It
turns a repository document written for a Git forge into an HTML fragment that
keeps working on a static site: headings get GitHub-compatible ``id`` anchors
with a "¶" self-link, ``.md`` links become ``.html`` links, local images are
queued for copying, and every local reference is recorded so that ``--check``
can validate it once all pages are known. Like the rest of md2html it imports
nothing from the project that vendors it.

Dependencies:
    markdown-it-py: CommonMark parser whose token stream lets links be
        rewritten and recorded before rendering, instead of editing HTML.
    mdit-py-plugins: Footnote, task-list and heading-anchor extensions from
        the same maintainers, so no Markdown syntax is parsed by hand.

Safety considerations:
    Targets are normalised and flagged when they escape SRC_DIR, so callers
    never copy files from outside it. Raw HTML in Markdown passes through
    unchanged (markdown-it still refuses ``javascript:`` link URLs), so only
    render documentation written by trusted authors.

Example:
    >>> github_slug("5.2 Mandatory persistence ports")
    '52-mandatory-persistence-ports'
    >>> page = render_page(make_parser(), "README.md", Path("."), None)
"""

import html
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
# github-slugger keeps letters, marks, decimal and letter numbers and connector
# punctuation such as "_", plus " " and "-"; every other character is dropped.
SLUG_KEEP = ("L", "M", "Nd", "Nl", "Pc")
REF_ATTRS = {"link_open": "href", "image": "src"}
# Rendered code escapes its quotes, so only real HTML attributes match here.
ANCHOR_ATTR = re.compile(r'\s(?:id|name)="([^"]*)"')


class Ref(NamedTuple):
    """A local link or image reference found while rendering. Immutable.

    Attributes:
        line (int): 1-based source line, for ``file:line`` diagnostics.
        href (str): The reference as written (after URL normalisation).
        target (str): Normalised POSIX target relative to SRC_DIR; it is
            ``..`` or starts with ``../`` when the reference escapes SRC_DIR.
        fragment (str): Percent-decoded ``#fragment``, or "" when absent.
    """

    line: int
    href: str
    target: str
    fragment: str


@dataclass
class Page:
    """One Markdown source file and what rendering it revealed.

    Created by ``render_page`` (or as a synthetic listing page) and treated
    as read-only once rendering has finished.

    Attributes:
        rel (str): POSIX source path relative to SRC_DIR, e.g. ``docs/a.md``.
        title (str): Plain text of the first H1, or ``rel`` without one.
        body (str): Rendered HTML content, without the page template.
        anchors (set[str]): Every ``id``/``name`` attribute value in ``body``.
        refs (list[Ref]): Local references in document order.
        images (set[str]): Local image targets to copy into OUT_DIR.
    """

    rel: str
    title: str = ""
    body: str = ""
    anchors: set[str] = field(default_factory=set)
    refs: list[Ref] = field(default_factory=list)
    images: set[str] = field(default_factory=set)


def github_slug(text: str) -> str:
    """Convert heading text to the anchor slug GitHub generates for it.

    Args:
        text (str): Plain heading text with Markdown markup already removed.

    Returns:
        str: Lowercase slug, before any ``-1``/``-2`` duplicate suffix.

    Raises:
        None.
    """
    keep = (c for c in text.lower() if c in " -" or unicodedata.category(c).startswith(SLUG_KEEP))
    return "".join(keep).replace(" ", "-")


def make_parser() -> MarkdownIt:
    """Build a CommonMark parser with the GitHub-style extensions enabled.

    Args:
        None.

    Returns:
        MarkdownIt: Parser with tables, strikethrough, task lists, footnotes
        and unique, self-linking heading anchors on every heading level.

    Raises:
        None.
    """
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    md.use(footnote_plugin).use(tasklists_plugin)
    # The plugin de-duplicates slugs within a page as -1, -2, ... like GitHub.
    md.use(anchors_plugin, max_level=6, slug_func=github_slug, permalink=True)
    # Wide tables scroll inside their own box so the page never scrolls sideways.
    md.add_render_rule(
        "table_open",
        lambda r, t, i, o, e: '<div class="table-wrap">\n' + r.renderToken(t, i, o, e),
    )
    md.add_render_rule("table_close", lambda r, t, i, o, e: r.renderToken(t, i, o, e) + "</div>\n")
    return md


def resolve_ref(page: Page, href: str, line: int, src: Path, blob: str | None) -> str:
    """Record one link or image reference and return the href to publish.

    Absolute URLs (any scheme, including ``mailto:``) are returned unchanged
    and not recorded. ``x.md`` becomes ``x.html``, images are queued for
    copying and, when ``blob`` is set, other existing source files point at
    the repository file view, because the site publishes only pages and images.

    Args:
        page (Page): Page being rendered; its ``refs``/``images`` grow.
        href (str): Reference as written, after markdown-it normalisation.
        line (int): 1-based source line of the reference.
        src (Path): SRC_DIR, used to recognise existing source files.
        blob (str | None): Repository file-view URL prefix ending in ``/``.

    Returns:
        str: The href to emit in the HTML.

    Raises:
        None.
    """
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return href
    path, new = unquote(parts.path), parts.path
    base = "" if path.startswith("/") else posixpath.dirname(page.rel)
    target = posixpath.normpath(posixpath.join(base, path.lstrip("/"))) if path else page.rel
    page.refs.append(Ref(line, href, target, unquote(parts.fragment)))
    inside = target != ".." and not target.startswith("../")
    if path.startswith("/"):
        # GitHub resolves "/x" from the repository root; make it page-relative
        # so the site still works when served from a sub-path like /project/.
        new = "../" * page.rel.count("/") + new.lstrip("/") or "./"
    if new.endswith(".md"):
        new = new[:-3] + ".html"
    elif inside and target.lower().endswith(IMAGE_SUFFIXES):
        page.images.add(target)
    elif inside and blob and path and (src / target).is_file():
        new = blob + quote(target)
    return href if new == parts.path else urlunsplit(("", "", new, parts.query, parts.fragment))


def render_page(md: MarkdownIt, rel: str, src: Path, blob: str | None) -> Page:
    """Parse one Markdown file, rewrite its local references and render it.

    Args:
        md (MarkdownIt): Parser from ``make_parser``.
        rel (str): POSIX path of the file relative to ``src``.
        src (Path): SRC_DIR.
        blob (str | None): Repository file-view URL prefix ending in ``/``.

    Returns:
        Page: The page with its title, body, anchors, refs and images.

    Raises:
        OSError: If the file cannot be read.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """
    page, env = Page(rel), {}
    tokens = md.parse((src / rel).read_text(encoding="utf-8-sig"), env)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h1" and not page.title:
            words = tokens[index + 1].children or []
            text = "".join(w.content for w in words if w.type in ("text", "code_inline"))
            page.title = text.strip()
        if token.type != "inline":
            continue
        # Inline tokens only know their block's first line, so count breaks.
        line = (token.map[0] if token.map else 0) + 1
        for child in token.children or []:
            line += child.type in ("softbreak", "hardbreak")
            if attr := REF_ATTRS.get(child.type):
                child.attrSet(attr, resolve_ref(page, str(child.attrs[attr]), line, src, blob))
    page.title = page.title or rel
    page.body = md.renderer.render(tokens, md.options, env)
    page.anchors = {html.unescape(a) for a in ANCHOR_ATTR.findall(page.body)}
    return page


def ref_ok(ref: Ref, src: Path, outputs: dict[str, Page]) -> bool:
    """Decide whether a recorded local reference will work on the built site.

    Args:
        ref (Ref): Reference recorded by ``resolve_ref``.
        src (Path): SRC_DIR.
        outputs (dict[str, Page]): Every HTML file written, keyed by its
            POSIX path relative to OUT_DIR.

    Returns:
        bool: True when the target is a written page whose anchors include
        any fragment, a directory with an index page, or an existing
        non-Markdown file inside SRC_DIR.

    Raises:
        None.
    """
    target = ref.target
    if target == ".." or target.startswith("../"):
        return False
    if (src / target).is_dir():
        target = posixpath.normpath(posixpath.join(target, "index.html"))
    dest = outputs.get(target[:-3] + ".html" if target.endswith(".md") else target)
    if dest is not None:
        return not ref.fragment or ref.fragment in dest.anchors
    return not target.endswith(".md") and (src / target).is_file()
