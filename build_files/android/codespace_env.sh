#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Source from the setup/build wrappers, before the existing Android env.sh.
ZFOLD_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export BUILD_BASE="$(realpath -m -- "${BUILD_BASE:-$(dirname "$ZFOLD_REPO_ROOT")/.zfold-build}")"
export ANDROID_HOME="$(realpath -m -- "${ANDROID_HOME:-$BUILD_BASE/android-sdk}")"
export ANDROID_NDK_VERSION=28.2.13676358
export ANDROID_NDK_ROOT="$(realpath -m -- "${ANDROID_NDK_ROOT:-$ANDROID_HOME/ndk/$ANDROID_NDK_VERSION}")"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
export ANDROID_HOST_CC="${ANDROID_HOST_CC:-/usr/bin/clang-18}"
export ANDROID_HOST_CXX="${ANDROID_HOST_CXX:-/usr/bin/clang++-18}"
export ANDROID_ABI=arm64-v8a ANDROID_API=31 ANDROID_TARGET_API=34
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
export PYTHONDONTWRITEBYTECODE=1
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/12.0/bin:$ANDROID_HOME/platform-tools:$PATH"
export TMPDIR="$BUILD_BASE/tmp"
# Do not inherit a different prefix or APK variant from a previous shell.
export LIBDIR="$BUILD_BASE/lib/android_arm64"
export BLENDER_ANDROID_CONFIG=full BLENDER_ANDROID_DEBUGGABLE=0 BLENDER_ANDROID_TURNIP=0
unset BLENDER_ANDROID_FLAVOUR
