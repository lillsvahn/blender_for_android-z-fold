# SPDX-License-Identifier: GPL-2.0-or-later
"""Cheap checks; no dependency/Blender compilation or API quota is consumed."""
import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
TESTS = pathlib.Path(__file__).resolve().parent
os.chdir(ROOT)
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def run(args):
    subprocess.run([str(x) for x in args], env=env, check=True)


for file in [*ROOT.glob('scripts/modules/bl_android_copilot/*.py'),
             ROOT / 'scripts/modules/bl_android_navigation.py', ROOT / 'scripts/startup/bl_android_zfold.py',
             *TESTS.glob('*.py'), ROOT / 'build_files/android/zfold_preflight.py']:
    ast.parse(file.read_text(), filename=str(file))
run(['git', 'diff', '--check'])
run([sys.executable, TESTS / 'test_core.py'])
for file in ['build_files/android/apk/package.sh', 'build_files/android/apk/sign.sh',
             'build_files/android/build_apk.sh', 'build_files/android/fastdeploy.sh', 'build_files/android/deps/build.sh']:
    run(['bash', '-n', file])
with tempfile.TemporaryDirectory(prefix='zfold-checks-') as temporary:
    binary = pathlib.Path(temporary) / 'mobile'
    run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
         '-Iintern/ghost/intern', TESTS / 'test_mobile.cc', '-o', binary])
    run([binary])
    jar = os.environ.get('ZFOLD_ANDROID_JAR')
    if not jar:
        jar = str(pathlib.Path(os.environ.get('ANDROID_HOME', '/nonexistent')) / 'platforms/android-35/android.jar')
    if pathlib.Path(jar).is_file():
        java = pathlib.Path(os.environ['JAVA_HOME']) / 'bin/java' if 'JAVA_HOME' in os.environ else 'java'
        run([java, 'com.sun.tools.javac.Main', '-Xlint:all', '-classpath', jar, '-source', '17', '-target', '17',
             '-d', temporary, *ROOT.glob('build_files/android/apk/app/src/main/java/org/blender/blender/*.java')])
    else:
        print('Java compile skipped: set ZFOLD_ANDROID_JAR or ANDROID_HOME to a provisioned SDK')
print('Z Fold cheap checks passed')
