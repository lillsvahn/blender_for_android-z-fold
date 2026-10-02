#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -euo pipefail
APK="${1:?usage: sign.sh APK}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../env.sh" >/dev/null
APKSIGNER="$ANDROID_HOME/build-tools/35.0.1/apksigner"
if [ -n "${BLENDER_ANDROID_KEYSTORE:-}" ]; then
  : "${BLENDER_ANDROID_KEYSTORE_PASSWORD:?Set persistent signing keystore password}"
  : "${BLENDER_ANDROID_KEY_ALIAS:?Set signing alias}"
  : "${BLENDER_ANDROID_KEY_PASSWORD:?Set signing key password}"
  [ -f "$BLENDER_ANDROID_KEYSTORE" ] || { echo "Signing keystore missing" >&2; exit 1; }
  "$APKSIGNER" sign --ks "$BLENDER_ANDROID_KEYSTORE" --ks-key-alias "$BLENDER_ANDROID_KEY_ALIAS" \
    --ks-pass env:BLENDER_ANDROID_KEYSTORE_PASSWORD --key-pass env:BLENDER_ANDROID_KEY_PASSWORD "$APK"
else
  if [ "${CI:-false}" = true ]; then
    echo "CI requires a persistent signing identity; refusing to generate a temporary key" >&2
    exit 1
  fi
  # Preserve upstream's stable local debug identity for existing developer builds.
  : "${BUILD_BASE:?Set BUILD_BASE for local debug signing}"
  KEYSTORE="$BUILD_BASE/android-debug.keystore"
  if [ ! -f "$KEYSTORE" ]; then
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KEYSTORE" -storepass android \
      -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
      -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1
  fi
  "$APKSIGNER" sign --ks "$KEYSTORE" --ks-key-alias androiddebugkey \
    --ks-pass pass:android --key-pass pass:android "$APK"
fi
"$APKSIGNER" verify "$APK"
