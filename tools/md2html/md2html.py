# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "markdown-it-py==4.2.0",
#     "mdit-py-plugins==0.6.1",
# ]
# ///
"""Build a static HTML site from every Markdown file in a directory tree.

Documentation in a repository is written for a Git forge, where ``.md`` links
and heading anchors just work. md2html renders each ``*.md`` under SRC_DIR to
HTML at the same relative path in OUT_DIR, adds ``index.html`` for directory
READMEs and an ``all-docs.html`` listing, and wraps every page in the
responsive light/dark ``template.html``, so the docs can be published (for
example on GitHub Pages) without a site framework. With ``--check`` it also
validates every internal link and ``#fragment``, so the same command doubles
as a documentation link test. This module assembles the site and owns the
command line; ``md2html_render.py`` renders individual pages. Neither imports
anything from the project that vendors md2html.

Dependencies:
    md2html_render: Sibling module with the Markdown parser configuration,
        GitHub-compatible heading slugs and link resolution.
    markdown-it-py, mdit-py-plugins: Pinned in the inline script metadata
        above so ``uv run`` builds a matching environment on demand.

Safety considerations:
    Files are written only beneath OUT_DIR and images are copied only from
    inside SRC_DIR. OUT_DIR is never emptied, so files left by earlier runs
    remain until the caller removes them. No timestamps are emitted, so
    reruns produce byte-identical output.

Example:
    $ uv run tools/md2html/md2html.py . /tmp/site --title vakr --check
    md2html: rendered 3 Markdown files into /tmp/site
    md2html: broken links: 0
"""

import argparse
import html
import posixpath
import shutil
import sys
from itertools import groupby
from pathlib import Path, PurePosixPath
from string import Template
from urllib.parse import quote

from md2html_render import Page, make_parser, ref_ok, render_page

ALWAYS_EXCLUDED = frozenset({".git", ".venv", "node_modules", "_site"})
TEMPLATE_PATH = Path(__file__).with_name("template.html")


def is_excluded(rel: str, globs: list[str]) -> bool:
    """Report whether a path matches any ``--exclude`` glob.

    Args:
        rel (str): POSIX path relative to SRC_DIR.
        globs (list[str]): Patterns matched against the whole path, where
            ``**`` spans directories, and against the bare name.

    Returns:
        bool: True when the path must be skipped.

    Raises:
        None.
    """
    path = PurePosixPath(rel)
    return any(path.full_match(g) or PurePosixPath(path.name).full_match(g) for g in globs)


def discover(src: Path, out: Path, globs: list[str]) -> list[str]:
    """Find the Markdown files to render, pruning excluded directories early.

    Args:
        src (Path): Directory searched recursively.
        out (Path): Output directory, skipped when it lies inside ``src``.
        globs (list[str]): Extra ``--exclude`` patterns.

    Returns:
        list[str]: Sorted POSIX paths relative to ``src``.

    Raises:
        None.
    """
    out_dir, found = out.resolve(), []
    for root, dirs, files in src.walk():
        here = PurePosixPath(root.relative_to(src).as_posix())
        # Pruning in place stops the walk from descending into skipped trees.
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in ALWAYS_EXCLUDED
            and (root / d).resolve() != out_dir
            and not is_excluded(str(here / d), globs)
        )
        names = (str(here / f) for f in files if f.endswith(".md"))
        found += [name for name in names if not is_excluded(name, globs)]
    return sorted(found)


def listing(pages: list[Page]) -> Page:
    """Build the "All docs" page: every page title, grouped by directory.

    Args:
        pages (list[Page]): All rendered pages.

    Returns:
        Page: Synthetic page for ``all-docs.html`` at the OUT_DIR root.

    Raises:
        None.
    """
    body = ["<h1>All docs</h1>"]
    ordered = sorted(pages, key=lambda p: (posixpath.dirname(p.rel), p.rel))
    for folder, group in groupby(ordered, key=lambda p: posixpath.dirname(p.rel)):
        body.append(f"<h2>{html.escape(folder + '/' if folder else 'Top level')}</h2>\n<ul>")
        body += [
            f'<li><a href="{quote(p.rel[:-3])}.html">{html.escape(p.title)}</a>'
            f" <code>{html.escape(p.rel)}</code></li>"
            for p in group
        ]
        body.append("</ul>")
    return Page("all-docs.html", title="All docs", body="\n".join(body) + "\n")


def output_map(pages: list[Page], index: Page) -> dict[str, Page]:
    """Map every HTML file to write onto the page that provides it.

    Args:
        pages (list[Page]): All rendered pages.
        index (Page): The ``all-docs.html`` listing page.

    Returns:
        dict[str, Page]: ``x.md`` gives ``x.html``; a ``README.md`` also gives
        its directory's ``index.html`` unless an ``index.md`` exists there.

    Raises:
        None.
    """
    sources = {p.rel for p in pages}
    outputs = {p.rel[:-3] + ".html": p for p in pages} | {"all-docs.html": index}
    for page in pages:
        folder = page.rel.removesuffix("README.md")
        if folder + "README.md" == page.rel and folder + "index.md" not in sources:
            outputs[folder + "index.html"] = page
    # Without a root README.md or index.md the "Home" link would be a 404, so
    # the listing doubles as the landing page.
    outputs.setdefault("index.html", index)
    return outputs


def fill(template: Template, name: str, page: Page, args: argparse.Namespace) -> str:
    """Wrap a page body in the HTML template.

    Args:
        template (Template): Template loaded from ``template.html``.
        name (str): Output path relative to OUT_DIR, for relative nav links.
        page (Page): Page whose title and body are inserted.
        args (argparse.Namespace): Options from ``parse_args``.

    Returns:
        str: The complete HTML document.

    Raises:
        KeyError: If the template uses an unknown placeholder.
    """
    up = "../" * name.count("/")
    source = html.escape(args.prefix + page.rel)
    if args.blob:
        source = f'<a href="{html.escape(args.blob + quote(page.rel))}">{source}</a>'
    return template.substitute(
        title=html.escape(page.title),
        site=html.escape(args.title),
        home=f"{up}index.html",
        all_docs=f"{up}all-docs.html",
        body=page.body,
        footer=f"Source: {source}" if page.rel.endswith(".md") else "",
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line and derive the source-link settings.

    Args:
        argv (list[str] | None): Arguments without the program name, or None
            to read ``sys.argv``.

    Returns:
        argparse.Namespace: Options plus ``prefix`` (SRC_DIR relative to its
        Git checkout, "" or ending in ``/``) and ``blob`` (file-view URL
        prefix ending in ``/``, or None without ``--repo-url``).

    Raises:
        SystemExit: On ``--help`` (status 0) or invalid arguments (status 2).
    """
    parser = argparse.ArgumentParser(
        prog="md2html.py", description="Render a Markdown tree as a static HTML site."
    )
    parser.add_argument("src", type=Path, metavar="SRC_DIR", help="tree to search for *.md")
    parser.add_argument("out", type=Path, metavar="OUT_DIR", help="where to write the site")
    parser.add_argument("--title", help="site title (default: SRC_DIR name)")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip; repeatable",
    )
    parser.add_argument("--repo-url", help="e.g. https://github.com/OWNER/REPO")
    parser.add_argument("--branch", default="main", help="branch for source links")
    parser.add_argument("--check", action="store_true", help="exit 1 on broken links")
    args = parser.parse_args(argv)
    if not args.src.is_dir():
        parser.error(f"SRC_DIR {args.src} is not a directory")
    here = args.src.resolve()
    args.title = args.title or here.name
    # Source links need the path from the repository root, which differs from
    # the SRC_DIR-relative path when only a subdirectory such as docs/ is built.
    root = next((p for p in (here, *here.parents) if (p / ".git").exists()), here)
    args.prefix = "" if root == here else here.relative_to(root).as_posix() + "/"
    repo = (args.repo_url or "").rstrip("/")
    args.blob = f"{repo}/blob/{quote(args.branch)}/{quote(args.prefix)}" if repo else None
    return args


def main(argv: list[str] | None = None) -> int:
    """Build the site and, with ``--check``, report broken internal links.

    Args:
        argv (list[str] | None): Arguments without the program name, or None
            to read ``sys.argv``.

    Returns:
        int: 0 on success, 1 when ``--check`` found broken links.

    Raises:
        SystemExit: On ``--help`` or invalid arguments.
        OSError: If a source cannot be read or an output cannot be written.
        UnicodeDecodeError: If a Markdown file is not valid UTF-8.
    """
    args = parse_args(argv)
    md, template = make_parser(), Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    sources = discover(args.src, args.out, args.exclude)
    pages = [render_page(md, rel, args.src, args.blob) for rel in sources]
    outputs = output_map(pages, listing(pages))
    for name, page in outputs.items():
        dest = args.out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(fill(template, name, page, args), encoding="utf-8", newline="\n")
    for image in sorted({i for p in pages for i in p.images if (args.src / i).is_file()}):
        (args.out / image).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.src / image, args.out / image)
    print(
        f"md2html: rendered {len(pages)} Markdown files into {args.out}",
        file=sys.stderr,
    )
    if not args.check:
        return 0
    broken = [
        f"{args.src / page.rel}:{ref.line}: broken link -> {ref.href}"
        for page in pages
        for ref in page.refs
        if not ref_ok(ref, args.src, outputs)
    ]
    # Flush so problems precede the stderr summary when both go to one CI log.
    print("\n".join(broken), end="\n" if broken else "", flush=True)
    print(f"md2html: broken links: {len(broken)}", file=sys.stderr)
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
