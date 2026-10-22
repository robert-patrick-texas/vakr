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

# nfpm v2.48.0, built statically. `go install` verifies every module against
# the Go checksum database, so a tampered module fails the build.
FROM golang:1.27.3@sha256:957861185dbf29d93668ace41e011d091c6e779953d47f0316ef2cc58e28fe2b AS nfpm
RUN CGO_ENABLED=0 GOBIN=/out go install github.com/goreleaser/nfpm/v2/cmd/nfpm@v2.48.0

# uv 0.13.4, pinned by image digest.
FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv

# Node.js 24.22.1 and its npm 11.19.1, pinned by image digest: the LTS line of
# web/.node-version. They only build the web UI (ADR-035); the package ships
# the built files and never Node. Change with web/.node-version and engines.
FROM node:24.22.1-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS node

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
