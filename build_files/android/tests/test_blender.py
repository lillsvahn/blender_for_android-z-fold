# SPDX-License-Identifier: GPL-2.0-or-later
"""Run using Blender --background --factory-startup --python-exit-code 1 --python FILE."""
import contextlib
import math
import pathlib
import sys
import types
import bpy
import _bpy
from mathutils import Quaternion, Vector

root = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'scripts/modules'), str(root / 'scripts/startup')]
from bl_android_copilot.registry import make_registry
from bl_android_navigation import navigate, geometry

registry = make_registry()
facts = registry.execute('inspect_scene', {})
assert facts['ok'] and facts['scene'] == bpy.context.scene.name
obj = registry.execute('inspect_object', {'name': 'Cube'})
assert obj['ok'] and obj['mesh']['vertices'] == 8
assert not registry.execute('inspect_object', {'name': 'MissingObject'})['ok']
stats = registry.execute('mesh_stats', {'scope': 'object', 'name': 'Cube'})
assert stats['ok'] and stats['triangles'] == 12, stats
cube = bpy.data.objects['Cube']
modifier = cube.modifiers.new('QA subdivision', 'SUBSURF')
modifier.levels = 1
bpy.context.view_layer.update()
stats = registry.execute('mesh_stats', {'scope': 'object', 'name': 'Cube'})
assert stats['ok'] and stats['triangles'] == 48, stats
cube.modifiers.remove(modifier)

bpy.context.preferences.edit.use_global_undo = True
bpy.ops.ed.undo_push(message='QA initial')
result = registry.execute('run_bpy', {'source': "bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name='CopilotTest'; print('created')"})
assert result['ok'] and result['success'] and result['stdout'] == 'created\n', result
assert bpy.data.objects.get('CopilotTest') is not None
result = registry.execute('undo', {})
assert result['ok'] and bpy.data.objects.get('CopilotTest') is None, result
result = registry.execute('run_bpy', {'source': "raise ValueError('QA exception')"})
assert result['ok'] and not result['success'] and 'QA exception' in result['traceback'], result
assert not registry.execute('run_bpy', {'source': 'bad syntax !'})['ok']

rv = types.SimpleNamespace(view_rotation=Quaternion((1, 0, 0), math.pi / 2),
                           view_location=Vector((0, 0, 0)), view_distance=10,
                           view_perspective='PERSP')
def eye():
    return rv.view_location + rv.view_rotation @ Vector((0, 0, rv.view_distance))
before = eye().copy()
forward = rv.view_rotation @ Vector((0, 0, -1))
navigate(rv, (0, 1, 0, 0), 0.2, 3, 1.6)
assert (eye() - before - forward * 0.6).length < 1e-5
assert rv.view_distance == 10
before = eye().copy()
navigate(rv, (0, 0, 1, 1), 0.2, 3, 1.6)
assert (eye() - before).length < 1e-5
assert abs((rv.view_rotation @ Vector((1, 0, 0))).z) < 1e-5  # No roll.
for _ in range(100):
    navigate(rv, (0, 0, 0, 1), 0.04, 3, 1.6)
assert (rv.view_rotation @ Vector((0, 0, -1))).z < 1
before = (eye().copy(), rv.view_rotation.copy())
for _ in range(20): navigate(rv, (0, 0, 0, 0), 0.04, 3, 1.6)
assert (eye() - before[0]).length < 1e-8 and rv.view_rotation == before[1]
area = types.SimpleNamespace(regions=[types.SimpleNamespace(type='UI', width=250, x=750)])
region = types.SimpleNamespace(width=1000, height=700, x=0)
geom = geometry(area, region, 140)
assert geom[1] == 750 and geom[4] + geom[6] < 750

# Platform functions only are mocked; registration/properties/undo/math above
# use real Blender RNA and operators. Register/unregister twice catches leaks.
_bpy.android_mobile_layout = lambda rows: None
_bpy.android_mobile_state = lambda: []
_bpy.android_copilot = lambda action, payload: '{"ok":true,"configured":false}'
sys.getandroidapilevel = lambda: 31
import bl_android_zfold as ui
for _ in range(2):
    ui.register()
    assert hasattr(bpy.context.window_manager, 'zfold')
    ui.unregister()
    assert not hasattr(bpy.types.WindowManager, 'zfold')
print('Z Fold real Blender checks passed:', bpy.app.version_string)
