# SPDX-License-Identifier: GPL-2.0-or-later
"""Cheap Codespaces tests: temporary fixtures only, never a Blender build."""
import base64
from contextlib import redirect_stdout
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('codespace_support', SCRIPT / 'codespace_support.py')
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='zfold-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / 'source'
        self.base = self.folder / 'build'
        self.root.mkdir()
        self.base.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(cs, 'ROOT', self.root).start()
        patch.dict(os.environ, BUILD_BASE=str(self.base), ANDROID_HOME=str(self.base / 'sdk'),
                   ANDROID_NDK_ROOT=str(self.base / 'sdk/ndk' / cs.NDK_VERSION),
                   LIBDIR=str(self.base / 'lib/android_arm64'), TMPDIR=str(self.base),
                   ANDROID_NDK_VERSION=cs.NDK_VERSION, ANDROID_API='31', ANDROID_ABI='arm64-v8a',
                   ANDROID_HOST_CC='/usr/bin/clang-18', ANDROID_HOST_CXX='/usr/bin/clang++-18',
                   JAVA_HOME='/fixture/java', CMAKE_BUILD_PARALLEL_LEVEL='2').start()

    def test_resource_profiles_and_resume(self):
        gib = cs.GIB
        self.assertFalse(cs.resource_failures(4, 16*gib, 128*gib, 86*gib, 0, 'setup'))
        for total in (32*gib, 64*gib):
            self.assertTrue(cs.resource_failures(8, 32*gib, total, 60*gib, 0, 'build'))
        self.assertTrue(cs.resource_failures(4, 8*gib, 128*gib, 100*gib, 0, 'setup'))
        self.assertTrue(cs.resource_failures(2, 16*gib, 128*gib, 100*gib, 0, 'setup'))
        self.assertTrue(cs.resource_failures(4, 16*gib, 128*gib, 84*gib, 0, 'setup'))
        self.assertTrue(cs.resource_failures(4, 16*gib, 128*gib, 69*gib, 0, 'build'))
        self.assertFalse(cs.resource_failures(4, 16*gib, 128*gib, 20*gib, 55*gib, 'build'))
        self.assertTrue(cs.resource_failures(4, 16*gib, 128*gib, 9*gib, 100*gib, 'build'))

    def test_disk_credit_requires_workspace_identity(self):
        product = self.base / cs.PRODUCT_DIRS[0]
        product.mkdir(parents=True)
        (product / 'existing.a').write_bytes(b'existing build')
        self.assertEqual(cs.owned_bytes(self.base, 'build'), 0)
        cs.init_workspace()
        self.assertGreater(cs.owned_bytes(self.base, 'build'), 0)
        cs.atomic_json(self.base / '.codespace-state/workspace.json', {'repo': 'other'})
        with self.assertRaises(cs.BuildError):
            cs.init_workspace()
        self.assertTrue((product / 'existing.a').is_file())

    def make_sdk(self):
        for _, folder, key, expected, binary in cs.sdk_components():
            (folder / binary).parent.mkdir(parents=True, exist_ok=True)
            (folder / binary).write_bytes(b'fixture')
            (folder / 'source.properties').write_text(f'{key or "Pkg.Revision"}={expected or "1"}\n')
        tools = Path(os.environ['ANDROID_HOME']) / 'cmdline-tools' / cs.CLI_VERSION
        (tools / 'bin').mkdir(parents=True)
        (tools / 'bin/sdkmanager').write_text('fixture: must never run when SDK is complete')
        (tools / 'source.properties').write_text('Pkg.Revision=' + cs.CLI_VERSION)

    def test_sdk_setup_skips_network_when_complete(self):
        self.make_sdk()
        with patch.object(cs, 'run') as run, patch.object(cs.subprocess, 'Popen') as popen:
            cs.install_sdk()
        run.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(cs.missing_sdk(), [])

    def test_repeat_setup_uses_build_reserve_after_sdk_is_complete(self):
        self.make_sdk()
        gib = cs.GIB
        usage = SimpleNamespace(total=128*gib, free=25*gib)
        with patch.object(cs, 'machine_resources', return_value=(4, 16*gib)), \
             patch.object(cs.shutil, 'disk_usage', return_value=usage), \
             patch.object(cs, 'owned_bytes', return_value=55*gib) as owned, \
             patch.object(cs, 'report_version'), patch.object(cs, 'run'), redirect_stdout(io.StringIO()):
            self.assertEqual(cs.resources('setup'), 15)
        self.assertTrue(all(call.args[1] == 'build' for call in owned.call_args_list))

    def test_sdk_requires_exact_revisions_and_components(self):
        self.make_sdk()
        sdk = Path(os.environ['ANDROID_HOME'])
        (sdk / 'build-tools/35.0.1/source.properties').write_text('Pkg.Revision=35.0.0')
        self.assertEqual(cs.missing_sdk(), ['build-tools;35.0.1'])
        with self.assertRaises(cs.BuildError):
            cs.validate_sdk()
        (sdk / 'ndk' / cs.NDK_VERSION / 'build/cmake/android.toolchain.cmake').unlink()
        self.assertIn('ndk;' + cs.NDK_VERSION, cs.missing_sdk())

    def test_interrupted_dependency_never_gets_completed_marker(self):
        recipe = self.root / 'build_files/android/deps/build.sh'
        recipe.parent.mkdir(parents=True)
        recipe.write_text('''set -eu
ALL_DEPS=(first second)
printf '%s\\n' "$1" >> "$BUILD_BASE/calls"
mkdir -p "$LIBDIR/$1/lib"
printf fixture > "$LIBDIR/$1/lib/fixture.a"
if [ "$1" = second ] && [ ! -f "$BUILD_BASE/allow-second" ]; then exit 19; fi
''')
        with self.assertRaises(subprocess.CalledProcessError):
            cs.dependencies()
        fingerprint = cs.dependency_fingerprint()
        self.assertTrue(cs.completed_dep('first', fingerprint))
        self.assertFalse(cs.completed_dep('second', fingerprint))
        (self.base / 'allow-second').touch()
        cs.dependencies()
        cs.dependencies()
        self.assertEqual((self.base / 'calls').read_text().splitlines(), ['first', 'second', 'second'])
        self.assertFalse(cs.completed_dep('first', 'different recipe/compiler fingerprint'))
        (cs.dep_prefix('first') / 'lib/fixture.a').write_bytes(b'')
        self.assertFalse(cs.completed_dep('first', fingerprint))

    def test_python_completion_also_requires_host_interpreter(self):
        prefix = cs.dep_prefix('python')
        prefix.mkdir(parents=True)
        (prefix / 'libpython.so').write_bytes(b'target')
        cs.atomic_json(self.base / '.codespace-state/deps/python.json',
                       {'fingerprint': 'fixture', 'files': [['libpython.so', 6]]})
        self.assertFalse(cs.completed_dep('python', 'fixture'))
        host = self.base / 'android_deps_build/work/python-host-install/bin/python3.13'
        host.parent.mkdir(parents=True)
        host.write_bytes(b'host')
        self.assertTrue(cs.completed_dep('python', 'fixture'))

    def test_interrupted_download_repair_retains_valid_and_cached_archives(self):
        downloads = self.base / 'android_deps_build/downloads'
        downloads.mkdir(parents=True)
        source = self.folder / 'fixture.txt'
        source.write_text('valid source must survive')
        valid = downloads / 'valid.tar.gz'
        with tarfile.open(valid, 'w:gz') as archive:
            archive.add(source, arcname='fixture.txt')
        broken = downloads / 'broken.tar.gz'
        broken.write_bytes(b'incomplete download')
        unrelated = downloads / 'keep-this.txt'
        unrelated.write_text('unrelated data must survive')
        cs.repair_downloads()
        self.assertTrue(valid.is_file())
        self.assertFalse(broken.exists())
        self.assertTrue(unrelated.is_file())
        with patch.object(cs.subprocess, 'run') as run:
            cs.repair_downloads()
        run.assert_not_called()

    def apk(self):
        apk = self.base / 'blender-full.apk'
        apk.write_bytes(b'fixture APK; publish test only')
        return apk

    def publish_info(self, abi="'arm64-v8a'", extra=''):
        return subprocess.CompletedProcess([], 0,
            "package: name='org.blender.blender' versionCode='5030100'\n" + extra +
            "native-code: " + abi + '\n', '')

    def test_publish_uses_hardlink_and_reports_result(self):
        apk = self.apk()
        output = io.StringIO()
        with patch.object(cs, 'run', return_value=self.publish_info()), redirect_stdout(output):
            cs.publish(apk)
        out = self.root / 'out/blender-zfold-v0.1.0-arm64.apk'
        self.assertEqual(out.stat().st_ino, apk.stat().st_ino)
        for label in ('BUILD SUCCESS', 'SHA256:', 'Size:', 'Z Fold 0.1.0', 'org.blender.blender', str(out)):
            self.assertIn(label, output.getvalue())

    def test_publish_falls_back_to_one_regular_copy(self):
        apk = self.apk()
        with patch.object(cs, 'run', return_value=self.publish_info()), patch.object(cs.os, 'link', side_effect=OSError), redirect_stdout(io.StringIO()):
            cs.publish(apk)
            cs.publish(apk)
        out = self.root / 'out/blender-zfold-v0.1.0-arm64.apk'
        self.assertEqual(out.read_bytes(), apk.read_bytes())
        self.assertNotEqual(out.stat().st_ino, apk.stat().st_ino)
        self.assertEqual(list(out.parent.iterdir()), [out])

    def test_publish_rejects_debug_or_additional_abis(self):
        for info in (self.publish_info(extra='application-debuggable\n'),
                     self.publish_info(abi="'arm64-v8a' 'x86_64'")):
            with patch.object(cs, 'run', return_value=info), self.assertRaises(cs.BuildError):
                cs.publish(self.apk())
        self.assertFalse((self.root / 'out').exists())

    def test_apk_certificate_must_match_persistent_identity(self):
        certificate = b'public certificate fixture'
        expected = hashlib.sha256(certificate).hexdigest()
        values = {'JAVA_HOME': '/fixture/java', 'BLENDER_ANDROID_KEYSTORE': '/fixture/temporary.jks',
                  'BLENDER_ANDROID_KEY_ALIAS': 'fixture-alias'}
        for digest in (expected, '0' * 64):
            results = [subprocess.CompletedProcess([], 0, certificate),
                       subprocess.CompletedProcess([], 0, 'Signer #1 certificate SHA-256 digest: ' + digest + '\n')]
            with patch.dict(os.environ, values), patch.object(cs, 'run', side_effect=results):
                if digest == expected:
                    cs.verify_apk(self.apk())
                else:
                    with self.assertRaisesRegex(cs.BuildError, 'APK signer differs'):
                        cs.verify_apk(self.apk())

    def make_repo(self):
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()
        git('init', '-b', 'zfold-v0.1.0')
        git('config', 'user.name', 'Codespaces unit fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'source').mkdir()
        (self.root / 'source/fixture.txt').write_text('finished runtime')
        git('add', '.')
        git('commit', '-m', 'finished runtime fixture')
        baseline = git('rev-parse', 'HEAD')
        patch.object(cs, 'BASELINE', baseline).start()
        patch.object(cs, 'RUNTIME_PATHS', ('source',)).start()
        git('update-index', '--add', '--cacheinfo', '160000,' + cs.HOST_PIN + ',lib/linux_x64')
        (self.root / 'lib/linux_x64').mkdir(parents=True)
        git('commit', '-m', 'Codespaces-only fixture')
        return git

    def test_source_guard_accepts_unchanged_runtime_without_network(self):
        git = self.make_repo()
        # A shallow repo is sufficient when the required ancestor is present.
        (self.root / '.git/shallow').write_text(cs.BASELINE + '\n')
        cs.verify_source()
        self.assertEqual(git('remote'), '')

    def test_source_guard_rejects_changed_runtime_and_wrong_branch(self):
        git = self.make_repo()
        (self.root / 'source/fixture.txt').write_text('unexpected runtime change')
        with self.assertRaisesRegex(cs.BuildError, 'uncommitted functional'):
            cs.verify_source(offline=True)
        git('add', 'source')
        git('commit', '-m', 'unexpected feature change')
        with self.assertRaisesRegex(cs.BuildError, 'Functional source differs'):
            cs.verify_source(offline=True)
        git('switch', '-c', 'main')
        with self.assertRaisesRegex(cs.BuildError, 'Select branch'):
            cs.verify_source(offline=True)

    def test_environment_paths_variants_and_tmp_are_consistent(self):
        env = dict(os.environ, BUILD_BASE=str(self.base / 'child/..'), ANDROID_HOME=str(self.base / 'sdk'),
                   BLENDER_ANDROID_DEBUGGABLE='1', BLENDER_ANDROID_TURNIP='1', BLENDER_ANDROID_FLAVOUR='-turnip')
        keys = ('BUILD_BASE', 'TMPDIR', 'LIBDIR', 'ANDROID_ABI', 'ANDROID_NDK_VERSION',
                'BLENDER_ANDROID_DEBUGGABLE', 'BLENDER_ANDROID_TURNIP', 'BLENDER_ANDROID_FLAVOUR')
        command = ('source "$1"; python3 -c \'import json,os; print(json.dumps({k:os.environ[k] '
                   'for k in ' + repr(keys).replace("'", '"') + ' if k in os.environ}))\'')
        result = subprocess.run(['bash', '-c', command, '_', str(SCRIPT / 'codespace_env.sh')],
                                env=env, check=True, capture_output=True, text=True)
        values = json.loads(result.stdout)
        self.assertEqual(values['BUILD_BASE'], str(self.base))
        self.assertEqual(values['TMPDIR'], str(self.base / 'tmp'))
        self.assertEqual(values['LIBDIR'], str(self.base / 'lib/android_arm64'))
        self.assertEqual(values['ANDROID_ABI'], 'arm64-v8a')
        self.assertEqual(values['ANDROID_NDK_VERSION'], cs.NDK_VERSION)
        self.assertEqual(values['BLENDER_ANDROID_DEBUGGABLE'], '0')
        self.assertEqual(values['BLENDER_ANDROID_TURNIP'], '0')
        self.assertNotIn('BLENDER_ANDROID_FLAVOUR', values)

    def wrapper_fixture(self, script, blocked):
        tools = self.folder / 'fake-tools'
        tools.mkdir(exist_ok=True)
        python = tools / 'python3'
        python.write_text('''#!/usr/bin/env bash
printf '%s\\n' "${2:-}" >> "$ZFOLD_FIXTURE_CALLS"
case "${2:-}" in
  resources) exit "$ZFOLD_FIXTURE_RESOURCE_EXIT" ;;
  prepare-signing) printf fixture > "$3/signing.jks"; exit 7 ;;
esac
''')
        python.chmod(0o755)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                   ZFOLD_FIXTURE_CALLS=str(self.base / 'calls'), ZFOLD_FIXTURE_RESOURCE_EXIT=str(blocked),
                   ZFOLD_KEYSTORE_BASE64='Zml4dHVyZQ==', ZFOLD_KEYSTORE_PASSWORD='unit-fixture-only',
                   ZFOLD_KEY_ALIAS='unit-fixture-only', ZFOLD_KEY_PASSWORD='unit-fixture-only')
        return subprocess.run(['bash', str(SCRIPT / script), '--check'], env=env, capture_output=True)

    def test_wrappers_stop_before_setup_or_signing_when_resources_fail(self):
        for script in ('codespace_setup.sh', 'codespace_build.sh'):
            (self.base / 'calls').unlink(missing_ok=True)
            result = self.wrapper_fixture(script, 18)
            self.assertEqual(result.returncode, 18)
            self.assertEqual((self.base / 'calls').read_text().splitlines(), ['resources'])
            self.assertFalse((self.base / '.codespace-state').exists())

    def test_build_wrapper_removes_private_signing_folder_on_failure(self):
        unrelated = self.base / 'tmp/keep-this.txt'
        unrelated.parent.mkdir()
        unrelated.write_text('unrelated work must remain')
        result = self.wrapper_fixture('codespace_build.sh', 0)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(list(unrelated.parent.iterdir()), [unrelated])
        self.assertIn('prepare-signing', (self.base / 'calls').read_text())


class SigningTests(unittest.TestCase):
    def test_missing_secrets_or_invalid_base64_stop_without_values(self):
        with patch.dict(os.environ, {name: '' for name in cs.SECRETS}):
            with self.assertRaises(cs.BuildError) as error:
                cs.signing_secrets()
            for name in cs.SECRETS:
                self.assertIn(name, str(error.exception))
        values = dict.fromkeys(cs.SECRETS, 'fixture-password-never-printed')
        values['ZFOLD_KEYSTORE_BASE64'] = 'invalid!base64!'
        with patch.dict(os.environ, values), self.assertRaises(cs.BuildError) as error:
            cs.signing_secrets()
        self.assertNotIn(values['ZFOLD_KEYSTORE_PASSWORD'], str(error.exception))
        self.assertNotIn(values['ZFOLD_KEYSTORE_BASE64'], str(error.exception))

    @unittest.skipUnless(shutil.which('java') and shutil.which('keytool'), 'JDK 17 needed for temporary key fixtures')
    def test_real_jks_and_pkcs12_private_key_validation(self):
        # Known unit-test passwords, fresh TEMPORARY keys. Never used to sign an
        # APK, never read real Codespaces credentials, never saved in the repo.
        java_home = Path(shutil.which('java')).resolve().parents[1]
        store_password = 'fixture-store-password'
        with tempfile.TemporaryDirectory(prefix='zfold-signing-fixture-') as temporary:
            base = Path(temporary)
            for kind in ('JKS', 'PKCS12'):
                key_password = store_password if kind == 'PKCS12' else 'fixture-key-password'
                fixture = base / (kind + '.keystore')
                env = dict(os.environ, ZFOLD_TEST_STORE_PASSWORD=store_password, ZFOLD_TEST_KEY_PASSWORD=key_password)
                result = subprocess.run([str(java_home / 'bin/keytool'), '-genkeypair', '-storetype', kind,
                    '-keystore', str(fixture), '-alias', 'unit-fixture', '-keyalg', 'RSA', '-keysize', '1024',
                    '-validity', '1', '-dname', 'CN=Codespaces unit fixture',
                    '-storepass:env', 'ZFOLD_TEST_STORE_PASSWORD', '-keypass:env', 'ZFOLD_TEST_KEY_PASSWORD'],
                    env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, 'temporary unit-fixture generation failed')
                encoded = base64.b64encode(fixture.read_bytes()).decode()
                values = {'JAVA_HOME': str(java_home), 'ZFOLD_KEYSTORE_BASE64': encoded,
                    'ZFOLD_KEYSTORE_PASSWORD': store_password, 'ZFOLD_KEY_ALIAS': 'unit-fixture',
                    'ZFOLD_KEY_PASSWORD': key_password, 'BLENDER_ANDROID_KEYSTORE_PASSWORD': store_password,
                    'BLENDER_ANDROID_KEY_ALIAS': 'unit-fixture', 'BLENDER_ANDROID_KEY_PASSWORD': key_password}
                good = base / (kind + '-valid')
                good.mkdir()
                output = io.StringIO()
                with patch.dict(os.environ, values), redirect_stdout(output):
                    cs.prepare_signing(good)
                self.assertEqual(stat.S_IMODE((good / 'signing.jks').stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(good.stat().st_mode), 0o700)
                self.assertFalse((good / 'VerifySigning.java').exists())
                self.assertNotIn(encoded, output.getvalue())
                self.assertNotIn(store_password, output.getvalue())
                for secret in ('BLENDER_ANDROID_KEYSTORE_PASSWORD', 'BLENDER_ANDROID_KEY_PASSWORD'):
                    bad = base / (kind + '-' + secret)
                    bad.mkdir()
                    with patch.dict(os.environ, dict(values, **{secret: 'incorrect-fixture-password'})):
                        with self.assertRaises(cs.BuildError) as error:
                            cs.prepare_signing(bad)
                    self.assertNotIn('incorrect-fixture-password', str(error.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
