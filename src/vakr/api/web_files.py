"""The built web UI, read once into a bounded in-memory index (ADR-035 #2, #3, #13; UI-012).

``vakr-api`` serves the static SvelteKit build same-origin outside
``/api/``. ``load_web_build`` reads the build directory once at startup:
every regular file (with its precompressed ``.br`` and ``.gz`` variants),
and the manifest ``vakr-web.json`` that the build writes, which names the
SHA-256 hashes of the inline bootstrap script for the page CSP. Requests
are answered from this index only, so no request path is ever joined to a
filesystem path: path traversal cannot reach a file outside the build.

The directory is ``/opt/vakr/share/web`` (``InstallPaths.web_dir``,
relocated by ``VAKR_ROOT``), or ``VAKR_WEB_DIR`` for development and tests
(Appendix E.31), the same pattern as ``VAKR_FIELDKIT``.

Dependencies:
    hashlib, json, pathlib: Reading and checking the build.
    vakr.paths: The installed location.

Safety:
    Bounded: at most ``MAX_FILES`` files and ``MAX_TOTAL_BYTES`` bytes are
    read (E.31); a larger or malformed build is refused with an operator
    sentence and the API serves without pages. Symbolic links and files
    outside the directory are never followed.

Example:
    >>> valid_hash("sha256-" + "A" * 43 + "=")
    True
"""

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from vakr.paths import InstallPaths

WEB_DIR_ENV = "VAKR_WEB_DIR"
"""Environment variable that points ``vakr-api`` at another build (development, tests)."""
MANIFEST = "vakr-web.json"
INDEX = "index.html"
IMMUTABLE_PREFIX = "_app/immutable/"
MAX_FILES = 2000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_HASHES = 16
_HASH = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")
_ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}  # fmt: skip


class WebBuildError(Exception):
    """The web build cannot be served; the message is an operator sentence."""


@dataclass(frozen=True, slots=True)
class WebFile:
    """One servable file.

    Attributes:
        body: The bytes as built.
        content_type: The ``Content-Type`` header value.
        etag: A strong validator of ``body`` (quoted).
        encoded: Precompressed variants by content coding (``br``, ``gzip``).
    """

    body: bytes
    content_type: str
    etag: str
    encoded: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebBuild:
    """The whole build.

    Attributes:
        files: Servable files by path relative to the build root, ``/``-separated.
        script_hashes: ``sha256-...`` sources of the inline bootstrap scripts.
    """

    files: Mapping[str, WebFile]
    script_hashes: tuple[str, ...]

    @property
    def index(self) -> WebFile:
        """The single-page app's fallback page.

        Args:
            None

        Returns:
            WebFile: ``index.html``.

        Raises:
            None
        """
        return self.files[INDEX]


def valid_hash(value: object) -> bool:
    """Tell whether a manifest entry is a well-formed CSP SHA-256 source.

    Args:
        value: The entry.

    Returns:
        bool: True for ``sha256-`` followed by 44 base64 characters.

    Raises:
        None
    """
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def web_directory(paths: InstallPaths, environ: Mapping[str, str] | None = None) -> Path:
    """Return where the build is: ``VAKR_WEB_DIR``, else ``/opt/vakr/share/web``.

    Args:
        paths: Install layout (``VAKR_ROOT`` relocates it).
        environ: Environment; ``None`` means ``os.environ``.

    Returns:
        Path: The build directory (it may not exist).

    Raises:
        None
    """
    env = os.environ if environ is None else environ
    override = env.get(WEB_DIR_ENV)
    return Path(override) if override else paths.web_dir


def _script_hashes(root: Path) -> tuple[str, ...]:
    """Read and check the manifest's script hashes.

    Args:
        root: The build directory.

    Returns:
        tuple[str, ...]: The hashes.

    Raises:
        WebBuildError: If the manifest is missing, unreadable, or malformed.
    """
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WebBuildError(f"{root / MANIFEST} is missing or not JSON ({type(exc).__name__}); "
                            "rebuild the web UI (npm run build)") from None  # fmt: skip
    hashes = data.get("script_hashes") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("format") != 1 or not isinstance(hashes, list)
            or len(hashes) > MAX_HASHES or not all(valid_hash(h) for h in hashes)):  # fmt: skip
        raise WebBuildError(f"{root / MANIFEST} is not a format-1 manifest of script hashes")
    return tuple(hashes)


def _candidates(root: Path) -> list[Path]:
    """List the regular files of the build, refusing one that is too large.

    Args:
        root: The resolved build directory.

    Returns:
        list[Path]: Files, sorted; symbolic links are skipped.

    Raises:
        WebBuildError: If there are more than ``MAX_FILES``.
    """
    found: list[Path] = []
    for directory, _dirs, names in os.walk(root, followlinks=False):
        for name in names:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                found.append(path)
        if len(found) > MAX_FILES * 3:
            raise WebBuildError(f"{root} holds more than {MAX_FILES} files; is it the web build?")
    return sorted(found)


def _read_files(root: Path) -> dict[str, WebFile]:
    """Read every servable file with its precompressed variants.

    Args:
        root: The resolved build directory.

    Returns:
        dict[str, WebFile]: Files by relative path.

    Raises:
        WebBuildError: If a bound is broken or a file cannot be read.
    """
    present = {p.relative_to(root).as_posix(): p for p in _candidates(root)}
    files: dict[str, WebFile] = {}
    total = 0
    for rel, path in present.items():
        if rel == MANIFEST or any(rel.endswith(suffix) for _, suffix in _ENCODINGS):
            continue
        try:
            body = path.read_bytes()
            encoded = {name: present[rel + suffix].read_bytes()
                       for name, suffix in _ENCODINGS if rel + suffix in present}  # fmt: skip
        except OSError as exc:
            raise WebBuildError(f"cannot read {path} ({exc.strerror})") from None
        total += len(body) + sum(len(v) for v in encoded.values())
        if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise WebBuildError(f"{root} is larger than {MAX_FILES} files or "
                                f"{MAX_TOTAL_BYTES // 2**20} MiB")  # fmt: skip
        kind = _TYPES.get(path.suffix.lower(), "application/octet-stream")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        files[rel] = WebFile(body, kind, etag, encoded)
    return files


def load_web_build(directory: Path) -> WebBuild:
    """Read the build directory into memory (blocking; call once at startup).

    Args:
        directory: The build directory.

    Returns:
        WebBuild: The index.

    Raises:
        WebBuildError: If the directory, ``index.html``, or the manifest is
            missing, or the build breaks a bound.
    """
    if not directory.is_dir():
        raise WebBuildError(f"no web UI build at {directory}")
    root = directory.resolve()
    hashes = _script_hashes(root)
    files = _read_files(root)
    if INDEX not in files:
        raise WebBuildError(f"{root / INDEX} is missing; rebuild the web UI (npm run build)")
    return WebBuild(files, hashes)
