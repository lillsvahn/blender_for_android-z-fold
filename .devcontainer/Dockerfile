# SPDX-License-Identifier: GPL-2.0-or-later
FROM mcr.microsoft.com/devcontainers/base:3-noble

# Minimal Python/LFS tools make the disk/RAM/source gates available before the much
# larger host compiler and Android SDK/NDK downloads in postCreateCommand.
# The base image's common tools don't guarantee Python on every image release.
USER root
RUN if ! command -v python3 >/dev/null 2>&1 || ! command -v git-lfs >/dev/null 2>&1; then \
      apt-get update -qq && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 git-lfs && \
      rm -rf /var/lib/apt/lists/*; \
    fi
USER vscode
