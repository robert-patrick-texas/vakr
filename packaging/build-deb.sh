#!/bin/bash
# Build vakr_<deb version>_amd64.deb in a clean ubuntu:26.04 container
# (PKG-001, PKG-004). `make deb` runs this; it needs docker, git, and uv.
#
# Usage: packaging/build-deb.sh [OUTPUT_DIR]        (default: dist)
# Environment:
#   VERSION_OVERRIDE  build as this version instead of VERSION. Test builds
#                     only (the upgrade test); tools/release refuses it.
#   BUILDER_IMAGE     tag for the builder image (default: vakr-deb-builder)
#   VAKR_UV_CACHE     docker volume caching uv downloads between builds
#                     (default: vakr-uv-cache; set empty to disable). Every
#                     download is hash-checked, so the cache cannot change output.
#   VAKR_BUILD_TRACE  set to 1 to trace the in-container build (bash -x)
#
# The source given to the container is every tracked file plus untracked,
# non-ignored files, so work in progress builds too; on a clean tree that is
# exactly the commit. SOURCE_DATE_EPOCH is the commit time, so two builds of
# one commit produce byte-identical packages.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
out_dir=${1:-dist}
mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd)
image=${BUILDER_IMAGE:-vakr-deb-builder}
cache=${VAKR_UV_CACHE-vakr-uv-cache}

version=${VERSION_OVERRIDE:-$(cat VERSION)}
# One tested mapping from VERSION to the Debian version (VER-002); it also
# rejects anything that is not MAJOR.MINOR.PATCH[-rc.N].
deb_version=$(uv run --quiet tools/release/release.py --print-version deb --version "$version")
test_build=0
if [ -n "${VERSION_OVERRIDE:-}" ]; then
    test_build=1
    echo "build-deb: WARNING: VERSION_OVERRIDE=$version; this is a test build" >&2
fi
epoch=$(git log -1 --format=%ct)

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
git ls-files -z --cached --others --exclude-standard |
    while IFS= read -r -d '' path; do
        # Tracked files deleted in the work tree are simply not built.
        if [ -e "$path" ]; then printf '%s\0' "$path"; fi
    done |
    tar --null --files-from=- --create --file="$stage/source.tar"
cp packaging/container/build.sh "$stage/build.sh"

rm -f "$out_dir/vakr_${deb_version}_amd64.deb" "$out_dir/vakr-fieldkit.pyz"
docker build --quiet --tag "$image" - <packaging/Dockerfile >/dev/null

cache_mount=()
if [ -n "$cache" ]; then cache_mount=(--volume "$cache:/root/.cache/uv"); fi
docker run --rm \
    --volume "$stage:/in:ro" --volume "$out_dir:/out" "${cache_mount[@]}" \
    --env VAKR_VERSION="$version" --env VAKR_DEB_VERSION="$deb_version" \
    --env VAKR_TEST_BUILD="$test_build" --env SOURCE_DATE_EPOCH="$epoch" \
    --env HOST_UID="$(id -u)" --env HOST_GID="$(id -g)" \
    "$image" bash ${VAKR_BUILD_TRACE:+-x} /in/build.sh

echo "build-deb: $out_dir/vakr_${deb_version}_amd64.deb"
