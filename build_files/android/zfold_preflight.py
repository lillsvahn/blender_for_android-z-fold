# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail before an expensive fresh Full ARM64 build on an unsuitable runner."""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--min-free-gib', type=int, default=70)
args = parser.parse_args()
failures = []
base = pathlib.Path(os.environ.get('BUILD_BASE', '')).resolve()
if not os.environ.get('BUILD_BASE'):
    failures.append('Set BUILD_BASE to a dedicated build workspace')
base.mkdir(parents=True, exist_ok=True)
free = shutil.disk_usage(base).free / 2**30
print(f'BUILD_BASE free: {free:.1f} GiB; required pre-build reserve: {args.min_free_gib} GiB')
if free < args.min_free_gib:
    failures.append('Insufficient disk for the documented ~60 GB build. Provision about 100 GiB free before SDK/source setup')
for tool in ('git', 'git-lfs', 'cmake', 'ninja', 'make', 'curl', 'patch', 'patchelf', 'zip', 'unzip', 'java', 'javac', 'pkg-config', 'bison', 'flex', 'autoconf', 'automake', 'libtoolize', 'meson'):
    if shutil.which(tool) is None:
        failures.append('Missing host tool: ' + tool)
compiler = os.environ.get('ANDROID_HOST_CXX', '')
if not compiler:
    compiler = next((tool for tool in ('g++-15', 'g++-14', 'clang++-18', 'clang++-17') if shutil.which(tool)), '')
if not compiler or shutil.which(compiler) is None:
    failures.append('Provide GCC >=14 or Clang >=17 host compiler')
else:
    version = subprocess.run([compiler, '--version'], capture_output=True, text=True).stdout
    match = re.search(r'(?:clang version |\)\s*)(\d+)\.', version)
    required = 17 if 'clang' in version.lower() else 14
    if not match or int(match[1]) < required:
        failures.append('Host compiler is too old (GCC >=14 or Clang >=17 required)')
if shutil.which('cmake'):
    version = subprocess.run(['cmake', '--version'], capture_output=True, text=True).stdout
    match = re.search(r'version (\d+)\.(\d+)', version)
    if not match or tuple(map(int, match.groups())) < (3, 26):
        failures.append('Full MaterialX/USD dependency build requires CMake >=3.26')
if shutil.which('java'):
    version = subprocess.run(['java', '-version'], capture_output=True, text=True).stderr
    match = re.search(r'version "(\d+)', version)
    if not match or int(match[1]) != 17:
        failures.append('Provision JDK 17 for the pinned Android packaging toolchain')
sdk = pathlib.Path(os.environ.get('ANDROID_HOME', '/nonexistent'))
ndk = pathlib.Path(os.environ.get('ANDROID_NDK_ROOT', str(sdk / 'ndk/28.2.13676358')))
for file in (sdk / 'platforms/android-35/android.jar', sdk / 'build-tools/35.0.1/aapt2',
             sdk / 'build-tools/35.0.1/d8', sdk / 'build-tools/35.0.1/apksigner',
             sdk / 'build-tools/35.0.1/zipalign', ndk / 'build/cmake/android.toolchain.cmake'):
    if not file.is_file():
        failures.append('Missing SDK/NDK component: ' + str(file))
if (ndk / 'source.properties').is_file() and '28.2.13676358' not in (ndk / 'source.properties').read_text():
    failures.append('NDK must match base revision 28.2.13676358')
if os.environ.get('ANDROID_ABI', 'arm64-v8a') != 'arm64-v8a':
    failures.append('This workflow builds ARM64 only')
if sys.platform != 'linux' or os.uname().machine != 'x86_64':
    failures.append('The manual CI workflow requires Linux x86_64 host tools')
if os.path.isfile('/proc/meminfo'):
    match = re.search(r'MemTotal:\s+(\d+)', pathlib.Path('/proc/meminfo').read_text())
    if match:
        ram = int(match[1]) / 2**20
        limit = pathlib.Path('/sys/fs/cgroup/memory.max')
        if limit.is_file() and limit.read_text().strip().isdigit():
            ram = min(ram, int(limit.read_text().strip()) / 2**30)
        print(f'Host RAM: {ram:.1f} GiB; use CMAKE_BUILD_PARALLEL_LEVEL=2 with 16 GiB')
        if ram < 14:
            failures.append('Provision at least 16 GB RAM for the Full dependency/Blender build')
if os.environ.get('CI') == 'true':
    for key in ('BLENDER_ANDROID_KEYSTORE', 'BLENDER_ANDROID_KEYSTORE_PASSWORD', 'BLENDER_ANDROID_KEY_ALIAS', 'BLENDER_ANDROID_KEY_PASSWORD'):
        if not os.environ.get(key):
            failures.append('Missing signing configuration: ' + key)
    if not pathlib.Path(os.environ.get('BLENDER_ANDROID_KEYSTORE', '/nonexistent')).is_file():
        failures.append('Signing keystore was not restored')
for failure in failures:
    print('BLOCKED:', failure, file=sys.stderr)
if failures:
    sys.exit(1)
print('Z Fold runner preflight passed')
