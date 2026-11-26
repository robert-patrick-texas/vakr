# Builder image for the vakr .deb (PKG-001, PKG-004).
#
# The package is assembled inside a clean Ubuntu 26.04 container so the result
# depends only on pinned inputs: uv (which pins and checksum-verifies the
# python-build-standalone interpreter), nfpm, Node.js for the web UI build,
# and the hashed lockfiles (uv.lock, web/package-lock.json). Tools
# that only inspect the result (lintian, systemd-analyze) come from the Ubuntu
# archive and do not affect package contents.
#
# Build it with no context: docker build -t vakr-deb-builder - < packaging/Dockerfile

ARG BASE_IMAGE=ubuntu:26.04

# nfpm v2.49.0, built statically. `go install` verifies every module against
# the Go checksum database, so a tampered module fails the build.
FROM golang:1.27.4@sha256:5f1b98fa02fe396ebce2a0227a0e08309e11792915b9dd2e1f71053d7e7e6666 AS nfpm
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/goreleaser/nfpm/v2/cmd/nfpm@v2.49.0

# uv 0.13.10, pinned by image digest.
FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

# Node.js 26.13.1 and its npm 11.20.0, pinned by image digest: the LTS line of
# web/.node-version. They only build the web UI (ADR-035); the package ships
# the built files and never Node. Change with web/.node-version and engines.
FROM node:26.13.1-trixie-slim@sha256:abe7e5a82b61a8ef67b315b85497c9e65173387ccf2f4a029248184b8e020be5 AS node

FROM ${BASE_IMAGE}
# binutils provides readelf for the glibc symbol-version gate in build.sh.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        binutils ca-certificates lintian systemd \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=nfpm /out/nfpm /usr/local/bin/nfpm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && node --version && npm --version
