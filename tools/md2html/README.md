# md2html

A small, portable Markdown-to-HTML site generator with a built-in link checker.

md2html renders every `*.md` file in a directory tree to a static HTML page at the same relative path. Heading anchors match GitHub's, `.md` links become `.html` links, and the pages are self-contained, responsive, and support light and dark themes. Every page has a left-hand index of all pages and, with `--repo-url`, a **GitHub** link in its top bar. Documentation written for a Git forge can be published as-is, for example to GitHub Pages. With `--check`, the same run validates every internal link and `#fragment` and fails on broken ones, so it doubles as a documentation link test in CI.

## Requirements

[uv](https://docs.astral.sh/uv/). The script declares its Python version (3.14 or newer) and its pinned dependencies as [PEP 723](https://peps.python.org/pep-0723/) inline metadata, so `uv run` fetches whatever is missing. There is nothing to install first.

## Usage

```sh
# Build the whole repository into _site/ and fail on broken links
uv run tools/md2html/md2html.py . _site --check

# Name the site, link each page to its source on GitHub, skip drafts
uv run tools/md2html/md2html.py . _site \
    --title myproject \
    --repo-url https://github.com/OWNER/REPO --branch main \
    --exclude 'drafts' --exclude 'docs/archive/**' \
    --check

# Build only docs/ somewhere temporary
uv run tools/md2html/md2html.py docs /tmp/site
```

When `--check` finds problems, it prints one line per problem and exits with status 1:

```text
docs/guide.md:42: broken link -> install.md#prerequisites
md2html: broken links: 1
```

| Argument | Meaning |
|---|---|
| `SRC_DIR` | Directory searched recursively for `*.md`. |
| `OUT_DIR` | Directory that receives the site. It is created if missing and never emptied, so files left by earlier runs remain until you remove them. |
| `--title NAME` | Site title shown in the top bar. Defaults to the name of `SRC_DIR`. |
| `--exclude GLOB` | Skips matching files and directories. Repeat the flag for more patterns. A pattern is matched against the path relative to `SRC_DIR` (`*` stays within one directory and `**` spans directories) and against the bare file or directory name, so `drafts` skips every directory called `drafts`. |
| `--repo-url URL` | Repository web URL, such as `https://github.com/OWNER/REPO`. Every page's top bar then links to it, and each page's footer links to `URL/blob/BRANCH/PATH`. Local links to files that are not published (such as `LICENSE` or source code) are pointed at the same file view. |
| `--branch NAME` | Branch used in those links. Default: `main`. |
| `--check` | Also validates internal links and exits 1 if any are broken. |

## What it generates

- **One page per Markdown file:** `a/b.md` becomes `a/b.html`. The page `<title>` is the first H1.
- **Directory index pages:** `README.md` also renders as `index.html` in the same directory, unless an `index.md` is there. If the root has neither, the listing below also serves as `index.html`, so the **Home** link always works.
- **`all-docs.html`:** every page title, grouped by directory.
- **Markdown flavor:** CommonMark plus GitHub-style tables, strikethrough, task lists (`- [ ]`), footnotes, and fenced code blocks with a `language-*` class. Raw HTML passes through unchanged, so render only documentation from authors you trust.
- **Heading anchors:** every heading gets GitHub's `id`. The text is lowercased, characters other than letters, digits, spaces, hyphens and underscores are dropped, and spaces become hyphens. Repeated headings get `-1`, `-2`, and so on. A "¶" self-link appears when you hover over a heading.
- **Links:** relative `x.md` and `x.md#frag` links become `x.html` and `x.html#frag`. Root-relative links (`/docs/x.md`) are resolved from `SRC_DIR` and made page-relative, so the site also works under a sub-path such as `/project/`. Absolute URLs, `mailto:`, and pure `#frag` links stay as they are.
- **Images:** local PNG, JPEG, GIF, SVG, and WebP files referenced from inside `SRC_DIR` are copied to the same relative path in `OUT_DIR`.
- **Page template:** [`template.html`](template.html) holds the page shell and all CSS inline. Nothing loads from a CDN, and no page uses JavaScript. It has a max-width text column, tables that scroll inside their own box, light and dark themes through `prefers-color-scheme`, a top bar, the page index on the left, and a "Source:" footer.
- **Top bar:** the site title, then **Home**, **All docs**, and, with `--repo-url`, a **GitHub** link to the repository. The link carries an inline GitHub mark (the Octicons `mark-github` icon, MIT License, GitHub Inc.) and `aria-label="GitHub repository"`. For a host whose name does not contain `github`, the link reads **Repository** and has no mark.
- **Page index:** every page has a `<nav aria-label="All pages">` listing every published page, labelled with its first H1 or, without one, its file name:
  - Pages in the root come first, then one collapsible `<details>` group per directory, nested like the tree (`docs/`, then `docs/architecture/` inside it, and so on).
  - Within a directory, `README.md` and `index.md` come first, then the other pages by file name, ignoring case. Ordering by file name lets authors sequence pages with prefixes such as `01-`.
  - A directory that holds only its `README.md` (a tool's directory, for example) appears as a link to that page rather than as a group of one.
  - The current page's link has `aria-current="page"` and a highlighted style, and the groups that contain it are open. All other groups start closed, so the index stays short as the site grows.
  - Links are relative (`../../docs/x.html`), so the site works from any base path. The "All docs" page marks nothing.
  - At 60rem (960px) and wider, the index is a sticky sidebar that scrolls on its own. At narrower widths it is hidden behind a **Pages** button in the top bar. The button is a CSS checkbox toggle, so it works without JavaScript, and the page never scrolls sideways.
- **Deterministic output:** pages contain no timestamps, so rerunning on the same input produces byte-identical files.

## What `--check` treats as broken

A link or image is broken unless one of the following is true:

- it points to a generated page, and its `#fragment`, if any, is an `id` or `name` on that page. Heading ids, footnote ids, and anchors written in raw HTML all count;
- it points to a directory that gets an `index.html`;
- it points to an existing file inside `SRC_DIR` that is not a Markdown file.

Links that leave `SRC_DIR`, and links to Markdown files that are excluded or missing, are reported. External URLs are not fetched.

## Using it in another project

The tool is self-contained in this directory. To reuse it, copy the whole directory:

```sh
cp -r path/to/this/repo/tools/md2html your-project/tools/
uv run your-project/tools/md2html/md2html.py your-project _site --check
```

The directory holds `md2html.py` (the entry point, site assembly, and command line), `md2html_render.py` (page rendering, heading slugs, and link resolution), `md2html_nav.py` (the page index and the repository link), `template.html`, the tests, and this README. Change the look by editing `template.html`. Its `${...}` placeholders are filled with Python's `string.Template`.

**Portability rule:** md2html must never import anything from the project that hosts it, and must never hard-code anything about that project. Every project-specific value (title, repository URL, branch, exclusions) comes from the command line. Keep it that way when you change it, so the directory can be copied between projects unchanged.

## Development

```sh
uv run --with pytest==9.2.0 --with markdown-it-py==4.2.0 --with mdit-py-plugins==0.6.1 \
    pytest tools/md2html
uvx ruff@0.17.3 check tools/md2html && uvx ruff@0.17.3 format --check tools/md2html
```

When you bump a dependency, change the pin in `md2html.py`'s inline metadata and in the test command together.

## Known limitations

- Links inside raw HTML (`<a href>`, `<img src>`) are neither rewritten nor checked.
- Footer source paths are computed from the Git checkout that contains `SRC_DIR`. Without a `.git` directory they are relative to `SRC_DIR`.
- Only lowercase `.md` files are rendered.
- The page index is repeated in every page, adding roughly 100 bytes per listed page to each one.
- Without JavaScript, the sidebar cannot scroll itself to the current page. On a very long index the current page can sit below the sidebar's fold, although its group is open.

## License

Apache License 2.0.
