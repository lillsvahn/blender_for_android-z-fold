#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Provision tools only. Never fetch Blender libraries or compile dependencies.
set +x
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
case "${1:-}" in
  --help) echo 'Usage: bash build_files/android/codespace_setup.sh [--check]'; exit 0 ;;
  ''|--check) ;;
  *) echo 'Unknown option; use --help' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo 'Too many arguments' >&2; exit 2; }
source "$SCRIPT_DIR/codespace_env.sh"
cd "$ZFOLD_REPO_ROOT"
# This executes before apt or SDK/NDK downloads, even on first creation.
python3 "$SCRIPT_DIR/codespace_support.py" resources --phase setup
if [ "${1:-}" = --check ]; then
  python3 "$SCRIPT_DIR/codespace_support.py" verify-source --offline
  python3 "$SCRIPT_DIR/codespace_support.py" preflight
  exit 0
fi
python3 "$SCRIPT_DIR/codespace_support.py" verify-source
if [ ! -d "$BUILD_BASE" ] && [ ! -w "$(dirname "$BUILD_BASE")" ]; then
  # Some devcontainer hosts leave the persistent /workspaces parent root-owned.
  # Create only our dedicated workspace, not a world-writable parent directory.
  sudo -n install -d -m 700 -o "$(id -u)" -g "$(id -g)" -- "$BUILD_BASE"
fi
mkdir -p "$BUILD_BASE/.codespace-state" "$BUILD_BASE/tmp"
exec 8>"$BUILD_BASE/.codespace-state/setup.lock"
flock -n 8 || { echo 'Another Z Fold setup is running. Wait for it, then rerun.' >&2; exit 1; }
python3 "$SCRIPT_DIR/codespace_support.py" init
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || {
  echo 'Use the supplied Ubuntu 24.04 devcontainer; no PPA or distribution upgrade is performed.' >&2
  exit 1
}
packages=(build-essential clang-18 cmake ninja-build git git-lfs openjdk-17-jdk-headless
  python3 python3-venv curl ca-certificates make patch patchelf zip unzip pkg-config
  bison flex autoconf automake libtool meson tar xz-utils bzip2 perl file
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev
  libncurses-dev uuid-dev)
missing=()
for package in "${packages[@]}"; do
  if [ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]; then
    missing+=("$package")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  admin=()
  [ "$EUID" -eq 0 ] || admin=(sudo -n)
  "${admin[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  "${admin[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
else
  echo 'Host packages already installed; apt download skipped.'
fi
python3 "$SCRIPT_DIR/codespace_support.py" install-sdk
python3 "$SCRIPT_DIR/codespace_support.py" preflight
printf '\nZ Fold Blender build environment ready.\n\nRun:\n\n  bash build_files/android/codespace_build.sh\n\nFull build does NOT start automatically.\n'
