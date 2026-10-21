"""A small fake web UI build for the serving tests (ADR-035 #3, #4, #13; WEB-T10).

``fake_build`` writes what ``npm run build`` writes, in miniature: the
fallback ``index.html`` with one inline bootstrap script, a content-hashed
module under ``_app/immutable/`` with ``.br`` and ``.gz`` variants, an
unhashed ``_app/version.json``, a favicon, and the ``vakr-web.json``
manifest naming the bootstrap's SHA-256 hash.

Dependencies:
    base64, gzip, hashlib, json: Building the files.

Example:
    >>> import tempfile, pathlib
    >>> root = fake_build(pathlib.Path(tempfile.mkdtemp()))
    >>> (root / "index.html").exists()
    True
"""

import base64
import gzip
import hashlib
import json
from pathlib import Path

BOOTSTRAP = (
    '\n\t\t\t\t{ __sveltekit_x = { base: "" }; import("/_app/immutable/entry/start.abc123.js"); }\n'
)
INDEX = (
    "<!doctype html><html lang=en><head><title>vakr</title>"
    '<link href="/_app/immutable/entry/start.abc123.js" rel="modulepreload"></head>'
    f"<body><div><script>{BOOTSTRAP}</script></div></body></html>"
)
MODULE = b"export const start = () => 'vakr';\n" * 20
MODULE_PATH = "_app/immutable/entry/start.abc123.js"


def script_hash(body: str) -> str:
    """Return the CSP source for an inline script, as the build's manifest script does.

    Args:
        body: The text between the script tags.

    Returns:
        str: ``sha256-<base64>``.

    Raises:
        None
    """
    return "sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()


def fake_build(root: Path, *, manifest: object | None = None) -> Path:
    """Write a miniature web build.

    Args:
        root: Directory to create it in (``web`` is made inside).
        manifest: Manifest content to write instead of the correct one.

    Returns:
        Path: The build directory.

    Raises:
        None
    """
    build = root / "web"
    module = build / MODULE_PATH
    module.parent.mkdir(parents=True)
    (build / "index.html").write_text(INDEX, encoding="utf-8")
    module.write_bytes(MODULE)
    (build / (MODULE_PATH + ".gz")).write_bytes(gzip.compress(MODULE, mtime=0))
    (build / (MODULE_PATH + ".br")).write_bytes(b"pretend-brotli")
    (build / "_app/version.json").write_text('{"version":"1"}', encoding="utf-8")
    (build / "favicon.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8"
    )
    content = (
        {"format": 1, "script_hashes": [script_hash(BOOTSTRAP)]} if manifest is None else manifest
    )
    (build / "vakr-web.json").write_text(json.dumps(content), encoding="utf-8")
    return build
