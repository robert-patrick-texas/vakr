#!/bin/bash
# Assemble /opt/vakr and package it with nfpm inside the clean builder
# container (PKG-001, PKG-002, PKG-004). packaging/build-deb.sh runs it; do
# not run it on a host, because it installs into /opt/vakr.
#
# Inputs: /in/source.tar (the staged tree) and these environment variables:
#   VAKR_VERSION       VERSION string, e.g. 7.2.0-rc.1
#   VAKR_DEB_VERSION   its Debian form, e.g. 7.2.0~rc.1 (tools/release, VER-002)
#   VAKR_TEST_BUILD    1 when VERSION_OVERRIDE set the version (test builds only)
#   SOURCE_DATE_EPOCH  commit time; every packaged file gets this mtime
#   HOST_UID, HOST_GID owner for the files written to /out
# Output: /out/vakr_<deb version>_amd64.deb, plus vakr-fieldkit.pyz if built.
set -euo pipefail

: "${VAKR_VERSION:?}" "${VAKR_DEB_VERSION:?}" "${SOURCE_DATE_EPOCH:?}"
export SOURCE_DATE_EPOCH
# Fixed hash seed for reproducible bytecode; no stray __pycache__ while vakr
# itself runs below (bytecode is compiled once, deliberately, at the end).
export PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
export UV_PYTHON_INSTALL_DIR=/build/python UV_NO_PROGRESS=1 UV_LINK_MODE=copy

OPT=/opt/vakr
PY=$OPT/python/bin/python3
PYVER=""  # from .python-version, so the bundled interpreter follows it (TOOLS-003)
SRC=/build/src
DEB=/build/out/vakr_${VAKR_DEB_VERSION}_amd64.deb

log() { printf '==> %s\n' "$*"; }
die() { printf 'build.sh: %s\n' "$*" >&2; exit 1; }

unpack_source() {
    mkdir -p "$SRC" /build/out "$OPT"
    tar -xf /in/source.tar -C "$SRC"
    cd "$SRC"
    if [ "${VAKR_TEST_BUILD:-0}" = 1 ]; then
        log "TEST BUILD: VERSION_OVERRIDE sets version $VAKR_VERSION (never release this)"
        printf '%s\n' "$VAKR_VERSION" >VERSION
    fi
    [ "$(cat VERSION)" = "$VAKR_VERSION" ] || die "VERSION is not $VAKR_VERSION"
    PYVER=$(tr -d '[:space:]' <.python-version)
    [[ $PYVER =~ ^3\.[0-9]+$ ]] || die ".python-version must be MAJOR.MINOR, not '$PYVER'"
}

install_python() {
    # uv pins the python-build-standalone release and verifies its checksum.
    log "installing Python $PYVER from python-build-standalone"
    uv python install --no-bin "$PYVER"
    local found
    found=$(uv python find --managed-python "$PYVER")
    mv "$(dirname "$(dirname "$(readlink -f "$found")")")" "$OPT/python"
    local lib=$OPT/python/lib stdlib=$OPT/python/lib/python$PYVER
    # Tk/IDLE need X11 and are useless on a headless monitor. Headers, the
    # config dir, *-config, pydoc, and libpython (the interpreter executable
    # links it statically; the .so only serves embedding) matter only for
    # building or embedding, never here: wheels are binary-only. The base
    # interpreter's pip goes too, so packages only enter through the venv.
    rm -rf "$stdlib"/{idlelib,tkinter,turtledemo,turtle.py} "$stdlib"/lib-dynload/_tkinter.* \
        "$lib"/{tcl,tk,itcl,thread}* "$lib"/lib{tcl,tk}* "$lib/pkgconfig" "$lib"/libpython3* \
        "$stdlib"/config-"$PYVER"-* "$OPT/python/include" "$stdlib"/site-packages/pip* \
        "$OPT/python/bin"/{idle,pip,pydoc}* "$OPT/python/bin"/python*-config
    "$PY" --version
}

smoke_test() {
    # Every remaining extension module and wheel must still load after pruning.
    log "smoke test: import every lib-dynload module and the runtime packages"
    "$OPT/venv/bin/python3" - <<'EOF'
import importlib, pathlib, sys
version = f"python{sys.version_info.major}.{sys.version_info.minor}"
dynload = pathlib.Path(sys.base_exec_prefix, "lib", version, "lib-dynload")
names = sorted(p.name.split(".")[0] for p in dynload.glob("*.so"))
assert names, f"no extension modules found in {dynload}"
# Most extensions are built into the interpreter; these cover its static libraries.
builtin = ["ssl", "sqlite3", "ctypes", "zlib", "bz2", "lzma", "decimal", "zoneinfo", "curses"]
wheels = ["cryptography.hazmat.bindings._rust", "_cffi_backend", "tomlkit", "vakr.cli.main"]
for name in [*names, *builtin, *wheels]:
    importlib.import_module(name)
print(f"imported {len(names) + len(builtin) + len(wheels)} modules on Python {sys.version}")
EOF
}

install_venv() {
    log "creating $OPT/venv with locked dependencies and the vakr wheel"
    "$PY" -m venv --without-pip "$OPT/venv"
    # A relative interpreter link keeps the venv valid wherever /opt/vakr is
    # inspected (lintian, an unpacked image); the .gitignore is noise here.
    ln -sfn ../../python/bin/python3 "$OPT/venv/bin/python3"
    rm -f "$OPT/venv/.gitignore"
    uv export --frozen --no-dev --no-group tools --no-emit-project --quiet \
        --format requirements.txt -o /build/requirements.txt
    uv pip install --python "$OPT/venv/bin/python3" --require-hashes --only-binary :all: \
        --no-deps -r /build/requirements.txt
    uv build --wheel --python "$PY" --out-dir /build/wheel \
        --build-constraint packaging/build-constraints.txt --require-hashes .
    # --no-cache: a cached wheel of the same name must never stand in for
    # this build's code.
    uv pip install --python "$OPT/venv/bin/python3" --no-deps --no-cache /build/wheel/vakr-*.whl
    # uv's install-time timestamp (uv_cache.json, listed in RECORD) is only
    # uv bookkeeping and would make two builds of one commit differ.
    local info
    info=$(echo "$OPT"/venv/lib/python"$PYVER"/site-packages/vakr-*.dist-info)
    rm -f "$info/uv_cache.json" "$OPT/venv/.lock"
    sed -i '/\/uv_cache\.json,/d' "$info/RECORD"
}

install_defaults() {
    local dir=$OPT/share/defaults rc=0
    mkdir -p "$dir"
    "$OPT/venv/bin/vakr" config defaults --output "$dir" || rc=$?
    case $rc in
        0) ;;
        2 | 3)
            # 3 is "not implemented"; 2 is the CLI skeleton's usage error for the
            # same missing subcommand. Either way the build continues, loudly.
            log "WARNING: 'vakr config defaults' unavailable (exit $rc); writing placeholder"
            printf '%s\n' "Placeholder: this vakr build predates packaged defaults (CFGM-001)." \
                "Every option still has a built-in default." >"$dir/README"
            ;;
        *) die "vakr config defaults failed with exit $rc" ;;
    esac
}

install_share() {
    local share=$OPT/share
    if [ -f tools/fieldkit/build_pyz.py ]; then
        log "building the field kit zipapp"
        mkdir -p "$share/fieldkit"
        "$PY" tools/fieldkit/build_pyz.py --output "$share/fieldkit/vakr-fieldkit.pyz"
        cp "$share/fieldkit/vakr-fieldkit.pyz" /build/out/
    else
        log "tools/fieldkit/build_pyz.py absent; no field kit in this build"
    fi
    install -D -m 0644 packaging/release-signers "$share/release-signers"
    install -D -m 0644 -t "$share/systemd" packaging/systemd/*
    install -D -m 0644 packaging/templates/config.toml "$share/templates/config.toml"
    install -D -m 0644 -t "$share/doc" LICENSE NOTICE README.md CHANGELOG.md
    write_debian_changelog
}

write_debian_changelog() {
    # Debian policy wants a Debian-format changelog in /usr/share/doc/vakr
    # (nfpm.yaml). It points at CHANGELOG.md, which stays the one real record.
    local maintainer
    maintainer=$(sed -n 's/^maintainer: //p' packaging/nfpm.yaml)
    mkdir -p /build/doc
    printf '%s\n' "vakr ($VAKR_DEB_VERSION) unstable; urgency=medium" "" \
        "  * Release $VAKR_VERSION. Release notes: /opt/vakr/share/doc/CHANGELOG.md" "" \
        " -- $maintainer  $(date -u -R -d "@$SOURCE_DATE_EPOCH")" |
        gzip -9 --no-name >/build/doc/changelog.gz
    touch --date="@$SOURCE_DATE_EPOCH" /build/doc/changelog.gz
}

normalize_tree() {
    log "compiling bytecode and normalising modes and mtimes"
    find "$OPT" -name __pycache__ -type d -prune -exec rm -rf {} +
    # Some wheels (pysnmp) ship macOS Finder metadata; it is never needed at runtime.
    find "$OPT" -name .DS_Store -type f -delete
    # Hash-based .pyc files stay valid whatever the source mtime is, so the
    # mtime clamp below cannot make Python rewrite them as root at runtime.
    # One process in sorted order: marshal output depends on string refcounts,
    # so spreading files over worker processes (-j 0) is not reproducible.
    "$PY" -m compileall -q -j 1 --invalidation-mode checked-hash \
        "$OPT/python/lib/python$PYVER" "$OPT/venv/lib/python$PYVER/site-packages"
    chmod -R u+rwX,go+rX,go-w "$OPT"
    # Shared libraries are mapped, never executed; dpkg and lintian expect 0644.
    find "$OPT" -type f \( -name '*.so' -o -name '*.so.*' \) -exec chmod a-x {} +
    find "$OPT" -exec touch --no-dereference --date="@$SOURCE_DATE_EPOCH" {} +
}

check_glibc_floor() {
    # The package declares libc6 (>= floor); nothing packaged may need newer.
    local floor newest
    floor=$(sed -n 's/.*- libc6 (>= \([0-9.]*\)).*/\1/p' packaging/nfpm.yaml)
    # readelf exits non-zero for the many non-ELF files; only its output matters.
    newest=$({ find "$OPT" -type f -exec readelf -V --wide {} + 2>/dev/null || true; } |
        grep -oE 'Name: GLIBC_[0-9.]+' | sed 's/Name: GLIBC_//' | sort -uV | tail -n 1)
    [ -n "$newest" ] || die "found no ELF files needing glibc; is readelf installed?"
    log "newest glibc symbol version needed: $newest (declared floor $floor)"
    dpkg --compare-versions "$newest" le "$floor" || die "raise libc6 floor in nfpm.yaml"
}

verify_units() {
    log "systemd-analyze verify"
    local out
    out=$(systemd-analyze verify --man=no --recursive-errors=no \
        "$SRC"/packaging/systemd/vakr{.target,-update.service,-update.timer} 2>&1) ||
        die "systemd-analyze verify failed: $out"
    [ -z "$out" ] || die "systemd-analyze verify reported: $out"
}

package_and_lint() {
    log "nfpm package -> $(basename "$DEB")"
    VAKR_DEB_VERSION=$VAKR_DEB_VERSION nfpm package --config packaging/nfpm.yaml \
        --packager deb --target "$DEB"
    log "lintian"
    # lintian warns when run as root; it only needs to read the package.
    runuser -u nobody -- env HOME=/tmp lintian --fail-on error --tag-display-limit 0 "$DEB"
    cp "$DEB" /out/
    [ ! -f /build/out/vakr-fieldkit.pyz ] || cp /build/out/vakr-fieldkit.pyz /out/
    chown "${HOST_UID:-0}:${HOST_GID:-0}" /out/*
    log "built $(basename "$DEB") ($(du -h "$DEB" | cut -f1))"
}

unpack_source
install_python
install_venv
install_defaults
install_share
smoke_test
normalize_tree
check_glibc_floor
verify_units
package_and_lint
