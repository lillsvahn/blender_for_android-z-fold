# SPDX-License-Identifier: GPL-2.0-or-later
"""Post-build check: reject stale scripts/Java/native bridge or wrong ABI."""
import hashlib
import io
import pathlib
import struct
import sys
import zipfile

root = pathlib.Path(__file__).resolve().parents[3]
apk_path = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(apk_path) as apk:
    assert apk.testzip() is None, 'APK ZIP CRC failed'
    payload = apk.read('assets/blender_runtime.zip')
    revision = apk.read('assets/blender_runtime.rev').decode().strip()
    assert revision == hashlib.sha256(payload).hexdigest()[:16], 'Runtime revision does not match payload'
    with zipfile.ZipFile(io.BytesIO(payload)) as runtime:
        assert runtime.testzip() is None, 'Runtime ZIP CRC failed'
        for path in [*root.glob('scripts/modules/bl_android_copilot/*.py'),
                     root / 'scripts/modules/bl_android_navigation.py', root / 'scripts/startup/bl_android_zfold.py']:
            relative = path.relative_to(root).as_posix()
            assert runtime.read(relative) == path.read_bytes(), 'Stale packaged script: ' + relative
    dex = apk.read('classes.dex')
    for marker in (b'CopilotBridge', b'toggleKeyboard', b'copilotCall'):
        assert marker in dex, 'Missing Java bridge: ' + marker.decode()
    native = apk.read('lib/arm64-v8a/libblender.so')
    assert native[:4] == b'\x7fELF' and native[4] == 2, 'Not an ELF64 library'
    assert struct.unpack('<H', native[18:20])[0] == 183, 'Not AArch64'
    for marker in (b'android_mobile_layout', b'android_mobile_state', b'android_copilot'):
        assert marker in native, 'Missing native/Python bridge: ' + marker.decode()
print('APK runtime revision, source bytes, Java/native bridges and ARM64 verified')
