# SPDX-License-Identifier: GPL-2.0-or-later
"""Codespaces provisioning/build helpers; no Blender runtime code is imported."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '0cff89a82f247faabc546f3a44a9b6b06a57d35e'
HOST_PIN = 'ecbd06cf6d2a4aa6b00a61ffb479fc81b17aba08'
NDK_VERSION = '28.2.13676358'
CLI_VERSION = '12.0'  # This sdkmanager runs on the required JDK 17.
CLI_URL = 'https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip'
# Official repository2-1.xml checksum for cmdline-tools;12.0 (Linux).
CLI_SHA1 = 'd313adb7aedccf6cf0cfca51ec180f0059f5f8f8'
SECRETS = ('ZFOLD_KEYSTORE_BASE64', 'ZFOLD_KEYSTORE_PASSWORD', 'ZFOLD_KEY_ALIAS', 'ZFOLD_KEY_PASSWORD')
PRODUCT_DIRS = ('lib/android_arm64', 'android_deps_build', 'build_host_tools_full',
                'build_android_full', 'android_apk_stage_full')
RUNTIME_PATHS = ('source', 'intern', 'scripts', 'assets', 'release',
                 'build_files/android/apk/app', 'CMakeLists.txt')
GIB = 2**30


class BuildError(RuntimeError):
    pass


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def git(*args, cwd=None):
    return run(['git', *args], cwd=cwd or ROOT, capture_output=True, text=True).stdout.strip()


def base_path():
    if not os.environ.get('BUILD_BASE'):
        raise BuildError('Source codespace_env.sh or run the setup/build wrapper to set BUILD_BASE')
    return Path(os.environ['BUILD_BASE']).resolve()


def ancestor(path):
    while not path.exists():
        path = path.parent
    return path


def identity():
    return {'repo': str(ROOT), 'baseline': BASELINE, 'host_lib': HOST_PIN}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf8')
    temporary.replace(path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return None


def init_workspace():
    base = base_path()
    path = base / '.codespace-state/workspace.json'
    old = read_json(path)
    if path.exists() and old != identity():
        raise BuildError('BUILD_BASE belongs to a different checkout/base. Choose a separate BUILD_BASE; nothing was deleted')
    atomic_json(path, identity())


def owned_bytes(base, phase):
    # Only credited on resume of a workspace we initialized. SDK isn't credited
    # against the build budget: it is already installed before the 70 GiB gate.
    if read_json(base / '.codespace-state/workspace.json') != identity():
        return 0
    paths = [base / p for p in PRODUCT_DIRS]
    if phase == 'setup':
        paths.append(Path(os.environ['ANDROID_HOME']))
    paths = sorted({p.resolve() for p in paths if p.exists()}, key=lambda p: len(p.parts))
    chosen = []
    for path in paths:
        if path.stat().st_dev == ancestor(base).stat().st_dev and not any(path.is_relative_to(p) for p in chosen):
            chosen.append(path)
    if not chosen:
        return 0
    # GNU du counts a hard-linked inode once across all these directories.
    output = run(['du', '-s', '-x', '-B1', *chosen], capture_output=True, text=True).stdout
    return sum(int(line.split()[0]) for line in output.splitlines())


def machine_resources():
    cpus = float(len(os.sched_getaffinity(0)))
    quota = Path('/sys/fs/cgroup/cpu.max')
    if quota.is_file():
        limit, period = quota.read_text().split()
        if limit != 'max':
            cpus = min(cpus, int(limit) / int(period))
    memory = int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text())[1]) * 1024
    for path in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        value = Path(path).read_text().strip() if Path(path).is_file() else ''
        if value.isdigit():
            memory = min(memory, int(value))
    return cpus, memory


def resource_failures(cpus, memory, total, free, credited, phase):
    failures = []
    if cpus < 4:
        failures.append('at least 4 available CPU cores')
    # Reserve for kernel/container overhead on a nominal 16 GB machine, as in
    # zfold_preflight.py. This is not an attempt to build on an 8 GB machine.
    if memory < 14 * GIB:
        failures.append('a machine with at least 16 GB RAM')
    if total < 100_000_000_000:
        failures.append('at least 100 GB total workspace storage (normally choose 128 GB)')
    reserve = (85 if phase == 'setup' else 70) * GIB
    if free < 10 * GIB or free + credited < reserve:
        failures.append(f'{reserve // GIB} GiB available before this phase, or that budget including existing Z Fold build products on resume; at least 10 GiB must remain free')
    return failures


def report_version(label, executable, flag='--version'):
    if shutil.which(str(executable)):
        result = subprocess.run([str(executable), flag], capture_output=True, text=True)
        lines = (result.stdout + result.stderr).splitlines()
        print(f'{label}: {lines[0] if lines else "unavailable"}')
    else:
        print(f'{label}: not installed yet')


def resources(phase='build'):
    base = base_path()
    sdk = Path(os.environ['ANDROID_HOME']).resolve()
    ndk = Path(os.environ['ANDROID_NDK_ROOT']).resolve()
    if base.is_relative_to(ROOT) or ROOT.is_relative_to(base):
        raise BuildError('BUILD_BASE must be outside the source checkout, on the same workspace disk')
    if ndk != sdk / 'ndk' / NDK_VERSION:
        raise BuildError('ANDROID_NDK_ROOT must be ANDROID_HOME/ndk/28.2.13676358')
    if any(ancestor(p).stat().st_dev != ROOT.stat().st_dev for p in (base, sdk, ndk)):
        raise BuildError('Checkout, BUILD_BASE and SDK/NDK must share the large workspace filesystem; do not use a separate /tmp volume')
    cpus, memory = machine_resources()
    usage = shutil.disk_usage(ancestor(base))
    manager = sdk / 'cmdline-tools' / CLI_VERSION / 'bin/sdkmanager'
    if phase == 'setup' and manager.is_file() and not missing_sdk() and properties(manager.parents[1] / 'source.properties').get('Pkg.Revision', '').strip() == CLI_VERSION:
        # After provisioning there are no large SDK downloads. Don't require
        # the extra first-setup reserve again after source/host LFS was fetched.
        phase = 'build'
        print('SDK already complete: checking the build/resume reserve, not the fresh SDK download budget')
    credited = owned_bytes(base, phase)
    print(f'CPU: {cpus:g} available; RAM: {memory / GIB:.1f} GiB', flush=True)
    run(['df', '-hT', ancestor(base)])
    print(f'Disk total: {usage.total / GIB:.1f} GiB; free: {usage.free / GIB:.1f} GiB; existing build products: {credited / GIB:.1f} GiB')
    for name in ('BUILD_BASE', 'ANDROID_HOME', 'ANDROID_NDK_ROOT', 'JAVA_HOME'):
        print(name + ': ' + os.environ[name])
    report_version('Host C++ compiler', os.environ['ANDROID_HOST_CXX'])
    report_version('Java', Path(os.environ['JAVA_HOME']) / 'bin/java', '-version')
    report_version('CMake', 'cmake')
    failures = resource_failures(cpus, memory, usage.total, usage.free, credited, phase)
    if sys.platform != 'linux' or os.uname().machine != 'x86_64':
        failures.append('Linux x86_64 (no emulation)')
    jobs = os.environ.get('CMAKE_BUILD_PARALLEL_LEVEL', '2')
    if not jobs.isdigit() or int(jobs) < 1 or int(jobs) > min(cpus, memory // (7 * GIB)):
        failures.append('CMAKE_BUILD_PARALLEL_LEVEL=2 (or a lower value); additional jobs need more RAM')
    if failures:
        raise BuildError('This Codespace is too small or incorrectly configured. Recreate it with sufficient resources. Required: ' + '; '.join(failures))
    return max(10, math.ceil(70 - owned_bytes(base, 'build') / GIB))


def preflight():
    minimum = resources('build')
    # Codespaces setup doesn't need release keys. The build wrapper separately
    # requires and validates persistent signing before fetching dependencies.
    run([sys.executable, ROOT / 'build_files/android/zfold_preflight.py', '--min-free-gib', minimum],
        env=dict(os.environ, CI='false'))
    validate_sdk()


def properties(path):
    if not path.is_file():
        return {}
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def sdk_components():
    sdk = Path(os.environ['ANDROID_HOME'])
    return [
        ('platforms;android-35', sdk / 'platforms/android-35', 'AndroidVersion.ApiLevel', '35', 'android.jar'),
        ('build-tools;35.0.1', sdk / 'build-tools/35.0.1', 'Pkg.Revision', '35.0.1', 'apksigner'),
        ('ndk;' + NDK_VERSION, sdk / 'ndk' / NDK_VERSION, 'Pkg.Revision', NDK_VERSION, 'build/cmake/android.toolchain.cmake'),
        ('platform-tools', sdk / 'platform-tools', None, None, 'adb'),
    ]


def missing_sdk():
    missing = []
    for package, folder, key, expected, binary in sdk_components():
        values = {k.strip(): v.strip() for k, v in properties(folder / 'source.properties').items()}
        if not (folder / binary).is_file() or (key and values.get(key) != expected):
            missing.append(package)
    return missing


def validate_sdk():
    missing = missing_sdk()
    if missing:
        raise BuildError('Missing/mismatched Android components: ' + ', '.join(missing) + '. Run codespace_setup.sh')


def install_sdk():
    sdk = Path(os.environ['ANDROID_HOME'])
    manager = sdk / 'cmdline-tools' / CLI_VERSION / 'bin/sdkmanager'
    if not manager.is_file():
        parent = manager.parents[2]
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='sdk-bootstrap-', dir=os.environ['TMPDIR']) as temporary:
            temporary = Path(temporary)
            archive = temporary / 'tools.zip'
            print('Installing pinned Android command-line tools 12.0 (JDK 17 compatible)')
            run(['curl', '--fail', '--location', '--retry', '2', '--connect-timeout', '20', '--max-time', '300', '--silent', '--show-error', CLI_URL, '-o', archive])
            if hashlib.sha1(archive.read_bytes()).hexdigest() != CLI_SHA1:
                raise BuildError('Android command-line tools checksum mismatch')
            with zipfile.ZipFile(archive) as file:
                file.extractall(temporary)
            tools = temporary / 'cmdline-tools'
            for executable in (tools / 'bin').iterdir():
                executable.chmod(0o755)
            destination = sdk / 'cmdline-tools' / CLI_VERSION
            if destination.exists():
                raise BuildError('Incomplete cmdline-tools/12.0 directory. Remove that directory only, then rerun setup')
            tools.rename(destination)
    revision = properties(manager.parents[1] / 'source.properties').get('Pkg.Revision', '').strip()
    if revision != CLI_VERSION:
        raise BuildError('Android command-line tools 12.0 revision mismatch')
    missing = missing_sdk()
    if not missing:
        print('SDK 35, build-tools 35.0.1 and NDK 28.2 already installed; SDK downloads skipped')
        return
    print('Accepting Android SDK licenses and installing: ' + ', '.join(missing))
    # sdkmanager's launcher forwards JAVA_OPTS. Keep its Java temporary files
    # on the workspace too, without JAVA_TOOL_OPTIONS echoing settings to logs.
    sdk_env = dict(os.environ, JAVA_OPTS=shlex.quote('-Djava.io.tmpdir=' + os.environ['TMPDIR']))
    # Avoid yes's expected SIGPIPE causing a failed setup under shell pipefail.
    answers = subprocess.Popen(['yes'], stdout=subprocess.PIPE)
    try:
        result = subprocess.run([str(manager), '--sdk_root=' + str(sdk), '--licenses'], stdin=answers.stdout,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=sdk_env)
    finally:
        answers.stdout.close()
        answers.terminate()
        answers.wait()
    if result.returncode:
        raise BuildError('sdkmanager license setup failed; check JDK 17 and network access')
    run([manager, '--sdk_root=' + str(sdk), *missing], env=sdk_env)
    validate_sdk()


def signing_secrets():
    missing = [name for name in SECRETS if not os.environ.get(name)]
    if missing:
        raise BuildError('Persistent signing required. Add these Codespaces secrets with repository access, then stop/restart the Codespace: ' + ', '.join(missing))
    try:
        data = base64.b64decode(''.join(os.environ['ZFOLD_KEYSTORE_BASE64'].split()), validate=True)
    except ValueError:
        raise BuildError('ZFOLD_KEYSTORE_BASE64 is not valid Base64') from None
    if not data or len(data) > 48 * 1024:
        raise BuildError('Invalid signing keystore size')


def prepare_signing(folder):
    signing_secrets()
    folder = Path(folder)
    folder.chmod(0o700)
    try:
        data = base64.b64decode(''.join(os.environ['ZFOLD_KEYSTORE_BASE64'].split()), validate=True)
    except ValueError:
        raise BuildError('ZFOLD_KEYSTORE_BASE64 is not valid Base64') from None
    if not data or len(data) > 48 * 1024:
        raise BuildError('Invalid signing keystore size')
    path = folder / 'signing.jks'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(data)
    tool = Path(os.environ['JAVA_HOME']) / 'bin/keytool'
    common = [tool, '-keystore', path, '-storepass:env', 'BLENDER_ANDROID_KEYSTORE_PASSWORD',
              '-alias', os.environ['BLENDER_ANDROID_KEY_ALIAS']]
    # keytool -certreq may ignore a different key password for PKCS12. Access
    # the private key through Java's Keystore API too, before any expensive work.
    verifier = folder / 'VerifySigning.java'
    verifier.write_text('''import java.io.File;
import java.security.KeyStore;
import java.security.PrivateKey;
class VerifySigning {
  public static void main(String[] args) {
    try {
      KeyStore store = KeyStore.getInstance(new File(args[0]),
          System.getenv("BLENDER_ANDROID_KEYSTORE_PASSWORD").toCharArray());
      if (!(store.getKey(System.getenv("BLENDER_ANDROID_KEY_ALIAS"),
              System.getenv("BLENDER_ANDROID_KEY_PASSWORD").toCharArray()) instanceof PrivateKey)) {
        System.exit(1);
      }
    } catch (Exception error) { System.exit(1); }
  }
}
''', encoding='utf8')
    for args in ([*common, '-list'], [tool.parent / 'java', verifier, path]):
        result = subprocess.run([str(x) for x in args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode:
            raise BuildError('Signing identity validation failed. Check keystore, alias and both Codespaces password secrets (values are never printed)')
    verifier.unlink()
    print('Persistent signing identity verified; private material is temporary and never logged')


def verify_source(offline=False):
    if git('branch', '--show-current') != 'zfold-v0.1.0':
        raise BuildError('Select branch zfold-v0.1.0; no automatic checkout/main merge is performed')
    absent = subprocess.run(['git', 'cat-file', '-e', BASELINE + '^{commit}'], cwd=ROOT, capture_output=True).returncode
    shallow = git('rev-parse', '--is-shallow-repository') == 'true'
    ancestry_missing = absent or subprocess.run(['git', 'merge-base', '--is-ancestor', BASELINE, 'HEAD'],
                                               cwd=ROOT, capture_output=True).returncode
    if (absent or (shallow and ancestry_missing)) and not offline:
        run(['git', 'fetch', '--no-tags', '--deepen=32', 'origin', 'zfold-v0.1.0'], cwd=ROOT)
    if subprocess.run(['git', 'cat-file', '-e', BASELINE + '^{commit}'], cwd=ROOT, capture_output=True).returncode:
        raise BuildError('The clone is too shallow to verify the finished v0.1.0. Run setup normally once to fetch branch history, then retry --check')
    if subprocess.run(['git', 'merge-base', '--is-ancestor', BASELINE, 'HEAD'], cwd=ROOT).returncode:
        raise BuildError('HEAD must descend from the completed 0cff89a v0.1.0; fetch more branch history if this is a shallow clone')
    for path in RUNTIME_PATHS:
        if git('rev-parse', 'HEAD:' + path) != git('rev-parse', BASELINE + ':' + path):
            raise BuildError('Functional source differs from completed v0.1.0: ' + path)
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *RUNTIME_PATHS], cwd=ROOT).returncode:
        raise BuildError('There are uncommitted functional source changes; restore/review them before this test build')
    if git('ls-files', '--others', '--exclude-standard', '--', *RUNTIME_PATHS):
        raise BuildError('Untracked runtime/source files could enter the APK; review/remove them before this test build')
    row = git('ls-tree', 'HEAD', 'lib/linux_x64').split()
    if len(row) < 3 or row[:3] != ['160000', 'commit', HOST_PIN]:
        raise BuildError('The original pinned lib/linux_x64 gitlink is missing/mismatched; use the finished Codespaces branch')
    print('Exact finished v0.1.0 runtime and original Linux host-library pin verified')


def lfs_complete(folder):
    for line in git('lfs', 'ls-files', '--long', cwd=folder).splitlines():
        _, _, name = line.split(maxsplit=2)
        path = folder / name
        if not path.is_file():
            return False
        with path.open('rb') as file:
            if file.read(128).startswith(b'version https://git-lfs.github.com/spec/v1'):
                return False
    return True


def fetch_sources():
    state = base_path() / '.codespace-state/sources.json'
    fingerprint = {p: git('rev-parse', 'HEAD:' + p) for p in ('assets', 'release', '.gitattributes')}
    fingerprint['host'] = HOST_PIN
    host = ROOT / 'lib/linux_x64'
    if read_json(state) == fingerprint and host.is_dir() and git('rev-parse', 'HEAD', cwd=host) == HOST_PIN and lfs_complete(ROOT) and lfs_complete(host):
        print('Pinned source/host LFS checkout already complete; downloads skipped')
        return
    # Command-scoped empty credentials for public projects.blender.org. Never
    # replace Codespaces' persisted GitHub credential helper, used for pushes.
    public = ['git', '-c', 'credential.helper=', '-c',
              'credential.helper=!f() { echo username=; echo password=; }; f']
    run(['git', 'lfs', 'install', '--local'], cwd=ROOT)
    run(['git', 'config', '--local', 'lfs.url', 'https://projects.blender.org/blender/blender.git/info/lfs'], cwd=ROOT)
    run([*public, 'lfs', 'pull'], cwd=ROOT)
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE='1', GIT_TERMINAL_PROMPT='0')
    run([*public, '-c', 'submodule.lib/linux_x64.update=checkout', 'submodule', 'update', '--init', '--depth=1', 'lib/linux_x64'], cwd=ROOT, env=env)
    if git('rev-parse', 'HEAD', cwd=host) != HOST_PIN:
        raise BuildError('Linux host-library checkout is not the pinned commit; refusing to build')
    run([*public, 'lfs', 'install', '--local'], cwd=host)
    run([*public, 'lfs', 'pull'], cwd=host)
    if not lfs_complete(ROOT) or not lfs_complete(host):
        raise BuildError('LFS assets are still pointers/missing; fix the public LFS download before building')
    atomic_json(state, fingerprint)


def dep_prefix(dep):
    base = base_path()
    if dep in ('numpy', 'certifi', 'pip'):
        return base / 'lib/android_arm64/python/lib/python3.13/site-packages' / dep
    if dep == 'ispc':
        return base / 'android_deps_build/host/ispc'
    return base / 'lib/android_arm64' / {'vulkan_headers': 'vulkan', 'oidn': 'openimagedenoise'}.get(dep, dep)


def dependency_fingerprint():
    digest = hashlib.sha256()
    files = [ROOT / 'build_files/android/deps/build.sh', ROOT / 'build_files/android/env.sh',
             ROOT / 'build_files/build_environment/cmake/versions.cmake',
             *sorted((ROOT / 'build_files/build_environment/patches').glob('*'))]
    for path in files:
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    digest.update(json.dumps({k: os.environ.get(k) for k in ('ANDROID_NDK_VERSION', 'ANDROID_API', 'ANDROID_ABI', 'ANDROID_HOST_CC', 'ANDROID_HOST_CXX')}, sort_keys=True).encode())
    return digest.hexdigest()


def completed_dep(dep, fingerprint):
    marker = read_json(base_path() / '.codespace-state/deps' / (dep + '.json'))
    if not marker or marker.get('fingerprint') != fingerprint:
        return False
    prefix = dep_prefix(dep)
    if dep == 'python' and not (base_path() / 'android_deps_build/work/python-host-install/bin/python3.13').is_file():
        return False
    return all((prefix / p).is_file() and (prefix / p).stat().st_size == size for p, size in marker.get('files', [])) and bool(marker.get('files'))


def download_archives():
    folder = base_path() / 'android_deps_build/downloads'
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and
                  p.name.endswith(('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.zip')))


def remember_downloads():
    atomic_json(base_path() / '.codespace-state/downloads.json',
                {p.name: [p.stat().st_size, p.stat().st_mtime_ns] for p in download_archives()})


def repair_downloads():
    # The existing fetch() writes to its final name. A killed curl can leave a
    # truncated archive which the next recipe would otherwise reuse forever.
    # Check only material not previously extracted successfully (or changed).
    state = base_path() / '.codespace-state/downloads.json'
    known = read_json(state) or {}
    for path in download_archives():
        metadata = [path.stat().st_size, path.stat().st_mtime_ns]
        if known.get(path.name) == metadata:
            continue
        args = ['unzip', '-tqq', path] if path.suffix == '.zip' else ['tar', '-tf', path]
        result = subprocess.run([str(x) for x in args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode < 0:
            raise BuildError('Download validation was interrupted; retained ' + path.name)
        if result.returncode:
            print('Removing incomplete/invalid download only: ' + path.name, flush=True)
            path.unlink()
            known.pop(path.name, None)
        else:
            known[path.name] = metadata
    atomic_json(state, known)


def dependencies():
    script = ROOT / 'build_files/android/deps/build.sh'
    names = re.search(r'ALL_DEPS=\((.*?)\)', script.read_text(), re.S)[1].split()
    if any(not re.fullmatch('[a-z0-9_]+', name) for name in names):
        raise BuildError('Unexpected dependency list; review the pinned recipe before building')
    fingerprint = dependency_fingerprint()
    checked_downloads = False
    for index, dep in enumerate(names, 1):
        if completed_dep(dep, fingerprint):
            print(f'[{index}/{len(names)}] {dep}: completed, reusing', flush=True)
            continue
        print(f'[{index}/{len(names)}] {dep}: building/resuming this recipe only', flush=True)
        if not checked_downloads:
            repair_downloads()
            checked_downloads = True
        # The legacy "all" heuristic only checks a nonempty install directory,
        # which can be half installed after interruption. Explicit names rebuild
        # that one recipe; our marker is written ONLY after a successful exit.
        try:
            run(['bash', script, dep], cwd=ROOT)
        except subprocess.CalledProcessError:
            repair_downloads()
            raise
        prefix = dep_prefix(dep)
        files = [[p.relative_to(prefix).as_posix(), p.stat().st_size]
                 for p in sorted(prefix.rglob('*')) if p.is_file()]
        if not files:
            raise BuildError('Dependency installed no files: ' + dep)
        atomic_json(base_path() / '.codespace-state/deps' / (dep + '.json'),
                    {'fingerprint': fingerprint, 'files': files})
        # Successful extraction/install already proved these archives readable;
        # don't decompress gigabytes of valid source again on the next resume.
        remember_downloads()
    print('All Android dependencies complete; existing work/host/prefix trees retained')


def verify_apk(apk):
    tools = Path(os.environ['ANDROID_HOME']) / 'build-tools/35.0.1'
    certificate = run([Path(os.environ['JAVA_HOME']) / 'bin/keytool', '-exportcert',
        '-keystore', os.environ['BLENDER_ANDROID_KEYSTORE'], '-storepass:env', 'BLENDER_ANDROID_KEYSTORE_PASSWORD',
        '-alias', os.environ['BLENDER_ANDROID_KEY_ALIAS']], capture_output=True).stdout
    expected = hashlib.sha256(certificate).hexdigest()
    verification = run([tools / 'apksigner', 'verify', '--print-certs', apk], capture_output=True, text=True).stdout
    signers = re.findall(r'^Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F]+)\s*$', verification, re.M)
    if [digest.lower() for digest in signers] != [expected]:
        raise BuildError('APK signer differs from the configured persistent signing identity')
    print('APK signature valid and certificate matches the configured persistent identity')


def publish(apk):
    apk = Path(apk).resolve()
    if not apk.is_file():
        raise BuildError('Build did not produce blender-full.apk')
    tools = Path(os.environ['ANDROID_HOME']) / 'build-tools/35.0.1'
    info = run([tools / 'aapt2', 'dump', 'badging', apk], capture_output=True, text=True).stdout
    package = re.search(r"^package:\s+name='([^']+)'\s+versionCode='([^']+)'", info, re.M)
    abi = re.search(r'^native-code:\s*(.*)$', info, re.M)
    if (not package or package.groups() != ('org.blender.blender', '5030100') or
            'application-debuggable' in info or not abi or re.findall(r"'([^']+)'", abi[1]) != ['arm64-v8a']):
        raise BuildError('APK application/version/ABI/debug flags differ from the Full ARM64 release')
    folder = ROOT / 'out'
    if folder.is_symlink():
        raise BuildError('out/ must be a normal directory inside the checkout')
    folder.mkdir(exist_ok=True)
    destination = folder / 'blender-zfold-v0.1.0-arm64.apk'
    temporary = folder / '.zfold-apk.tmp'
    temporary.unlink(missing_ok=True)
    try:
        try:
            os.link(apk, temporary)
            storage = 'hard link; no extra APK data copy'
        except OSError:
            shutil.copyfile(apk, temporary)
            storage = 'download copy (hard links unavailable); removable after download'
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with destination.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    print(f'\nBUILD SUCCESS\n\nAPK:\n{destination}\n\nSize:\n{destination.stat().st_size / 2**20:.2f} MiB\n\nSHA256:\n{digest.hexdigest()}\n\nVersion:\nZ Fold 0.1.0\n\nApplication ID:\norg.blender.blender\n\nOriginal APK:\n{apk}\n\nStorage:\n{storage}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('resources', 'init', 'preflight', 'install-sdk',
                         'signing-secrets', 'prepare-signing', 'verify-source', 'fetch-sources', 'dependencies', 'verify-apk', 'publish'))
    parser.add_argument('path', nargs='?')
    parser.add_argument('--phase', choices=('setup', 'build'), default='build')
    parser.add_argument('--offline', action='store_true', help='verify-source: do not deepen a shallow clone')
    args = parser.parse_args()
    if args.command in ('prepare-signing', 'verify-apk', 'publish') and not args.path:
        parser.error(args.command + ' requires a path')
    commands = {'resources': lambda: resources(args.phase), 'init': init_workspace,
                'preflight': preflight, 'install-sdk': install_sdk, 'signing-secrets': signing_secrets,
                'prepare-signing': lambda: prepare_signing(args.path), 'verify-source': lambda: verify_source(args.offline),
                'fetch-sources': fetch_sources, 'dependencies': dependencies,
                'verify-apk': lambda: verify_apk(args.path), 'publish': lambda: publish(args.path)}
    try:
        commands[args.command]()
    except (BuildError, OSError, ValueError, subprocess.CalledProcessError) as ex:
        sys.stdout.flush()
        print('BLOCKED: ' + str(ex), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
