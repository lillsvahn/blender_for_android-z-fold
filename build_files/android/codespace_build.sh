#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Explicit one-command release build. No Actions runner or service is used.
set +x
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
case "${1:-}" in
  --help)
    echo 'Usage: bash build_files/android/codespace_build.sh [--check]'
    echo '--check: validate resources/tools/signing only; no downloads or dependency/APK compilation.'
    exit 0 ;;
  ''|--check) ;;
  *) echo 'Unknown option; use --help' >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo 'Too many arguments' >&2; exit 2; }
source "$SCRIPT_DIR/codespace_env.sh"
cd "$ZFOLD_REPO_ROOT"
python3 "$SCRIPT_DIR/codespace_support.py" resources --phase build
python3 "$SCRIPT_DIR/codespace_support.py" signing-secrets
if [ "${1:-}" = --check ]; then
  python3 "$SCRIPT_DIR/codespace_support.py" verify-source --offline
else
  python3 "$SCRIPT_DIR/codespace_support.py" verify-source
fi
if [ "${1:-}" = --check ]; then
  python3 "$SCRIPT_DIR/codespace_support.py" preflight
else
  bash "$SCRIPT_DIR/codespace_setup.sh"
fi
mkdir -p "$BUILD_BASE/.codespace-state" "$BUILD_BASE/tmp"
exec 9>"$BUILD_BASE/.codespace-state/build.lock"
flock -n 9 || { echo 'A Z Fold build already owns this workspace. Do not start a second build.' >&2; exit 1; }
SIGN_DIR="$(mktemp -d "$BUILD_BASE/tmp/signing.XXXXXXXX")"
cleanup() { rm -rf -- "$SIGN_DIR"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export BLENDER_ANDROID_KEYSTORE="$SIGN_DIR/signing.jks"
export BLENDER_ANDROID_KEYSTORE_PASSWORD="$ZFOLD_KEYSTORE_PASSWORD"
export BLENDER_ANDROID_KEY_ALIAS="$ZFOLD_KEY_ALIAS"
export BLENDER_ANDROID_KEY_PASSWORD="$ZFOLD_KEY_PASSWORD"
python3 "$SCRIPT_DIR/codespace_support.py" prepare-signing "$SIGN_DIR"
# No Base64 private material is needed by any later child process.
unset ZFOLD_KEYSTORE_BASE64 ZFOLD_KEYSTORE_PASSWORD ZFOLD_KEY_ALIAS ZFOLD_KEY_PASSWORD
python3 "$SCRIPT_DIR/codespace_support.py" preflight
if [ "${1:-}" = --check ]; then
  echo 'CHECKS PASSED. No LFS/dependency/APK build started.'
  exit 0
fi
python3 "$SCRIPT_DIR/tests/run_checks.py"
python3 "$SCRIPT_DIR/tests/test_codespaces.py"
python3 "$SCRIPT_DIR/codespace_support.py" fetch-sources
python3 "$SCRIPT_DIR/codespace_support.py" preflight
python3 "$SCRIPT_DIR/codespace_support.py" dependencies
python3 "$SCRIPT_DIR/codespace_support.py" preflight
python3 "$SCRIPT_DIR/build.py" full --reconfigure --repackage --no-archive
APK="$BUILD_BASE/android_apk_stage_full/blender-full.apk"
python3 "$SCRIPT_DIR/tests/check_apk.py" "$APK"
python3 "$SCRIPT_DIR/codespace_support.py" verify-apk "$APK"
python3 "$SCRIPT_DIR/codespace_support.py" publish "$APK"
