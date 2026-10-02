# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider-independent local Blender tools. All executors run on the main thread."""
import base64
import contextlib
import io
import math
import struct
import threading
import traceback
import zlib
from dataclasses import dataclass
import bpy


@dataclass(frozen=True)
class BlenderTool:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    executor: object
    mutating: bool = False


def _validate(value, schema, path='input'):
    kind = schema.get('type')
    types = {'object': dict, 'string': str, 'boolean': bool, 'integer': int, 'number': (int, float)}
    if kind in types and (not isinstance(value, types[kind]) or
                          (kind in {'integer', 'number'} and isinstance(value, bool))):
        raise ValueError(f'{path} must be {kind}')
    if kind == 'object':
        props = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError(f'{path}.{key} is required')
        for key, item in value.items():
            if key not in props:
                raise ValueError(f'Unknown input field {path}.{key}')
            _validate(item, props[key], f'{path}.{key}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'Invalid {path}')
    if kind in {'integer', 'number'}:
        if not math.isfinite(value) or not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
            raise ValueError(f'{path} out of range')
    if kind == 'string' and len(value) > schema.get('maxLength', 200000):
        raise ValueError(f'{path} too long')


class BlenderToolRegistry:
    def __init__(self):
        self.tools = {}

    def add(self, tool):
        if tool.name in self.tools:
            raise ValueError('Duplicate tool ' + tool.name)
        self.tools[tool.name] = tool

    def validate(self, name, inputs):
        if name not in self.tools:
            raise ValueError('Unknown tool ' + name)
        _validate(inputs, self.tools[name].input_schema)

    def execute(self, name, inputs, context=None):
        try:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError('Blender tools require the main thread')
            self.validate(name, inputs)
            result = self.tools[name].executor(context or bpy.context, **inputs)
            return {'ok': True, **result}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)[:2000], 'traceback': traceback.format_exc()[-6000:]}


def _vec(value):
    return [round(float(x), 6) for x in value]


def _object(ctx, name):
    obj = ctx.scene.objects.get(name)
    if obj is None:
        raise ValueError(f'Object {name!r} does not exist in the current scene')
    return obj


def _viewport(ctx):
    if ctx.area and ctx.area.type == 'VIEW_3D' and ctx.region and ctx.region.type == 'WINDOW':
        return ctx.window, ctx.area, ctx.region
    if ctx.area and ctx.area.type == 'VIEW_3D':
        for region in ctx.area.regions:
            if region.type == 'WINDOW' and region.data:
                return ctx.window, ctx.area, region
    if ctx.window:
        for area in ctx.window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW' and region.data:
                        return ctx.window, area, region
    raise ValueError('No suitable 3D Viewport in the current window')


def inspect_scene(ctx, limit=40, offset=0):
    objects = list(ctx.scene.objects)
    collections, queue, seen = [], [ctx.scene.collection], set()
    while queue and len(collections) < 80:
        collection = queue.pop(0)
        if collection.as_pointer() in seen:
            continue
        seen.add(collection.as_pointer())
        collections.append({'name': collection.name, 'objects': len(collection.objects)})
        queue.extend(collection.children)
    result = {
        'scene': ctx.scene.name, 'mode': ctx.mode,
        'active_object': ctx.active_object.name if ctx.active_object else None,
        'selected_objects': [o.name for o in ctx.selected_objects][:80],
        'objects': [{'name': o.name, 'type': o.type, 'location': _vec(o.location),
                     'dimensions': _vec(o.dimensions)} for o in objects[offset:offset + limit]],
        'object_count': len(objects), 'offset': offset,
        'truncated': offset + limit < len(objects),
        'collections': collections,
        'units': {'system': ctx.scene.unit_settings.system, 'scale_length': ctx.scene.unit_settings.scale_length},
    }
    try:
        _, area, region = _viewport(ctx)
        result['viewport'] = {'shading': area.spaces.active.shading.type,
                              'perspective': region.data.view_perspective,
                              'view_location': _vec(region.data.view_location),
                              'view_distance': region.data.view_distance,
                              'lens': area.spaces.active.lens}
    except ValueError:
        result['viewport'] = None
    return result


def inspect_object(ctx, name):
    obj = _object(ctx, name)
    result = {'name': obj.name, 'type': obj.type, 'location': _vec(obj.location),
              'rotation_euler': _vec(obj.rotation_euler), 'rotation_mode': obj.rotation_mode,
              'rotation_quaternion': _vec(obj.rotation_quaternion), 'scale': _vec(obj.scale),
              'dimensions': _vec(obj.dimensions), 'matrix_world': [_vec(r) for r in obj.matrix_world],
              'bounding_box_world': [_vec(obj.matrix_world @ __import__('mathutils').Vector(v)) for v in obj.bound_box],
              'parent': obj.parent.name if obj.parent else None,
              'children': [o.name for o in obj.children][:80],
              'modifiers': [{'name': m.name, 'type': m.type, 'viewport': m.show_viewport,
                             'render': m.show_render} for m in obj.modifiers],
              'materials': [s.material.name if s.material else None for s in obj.material_slots]}
    if obj.type == 'MESH':
        result['mesh'] = {'vertices': len(obj.data.vertices), 'edges': len(obj.data.edges), 'faces': len(obj.data.polygons)}
    return result


def mesh_stats(ctx, scope='selected', name='', collection='', evaluated=True):
    if scope == 'object':
        objects = [_object(ctx, name)]
    elif scope == 'selected':
        objects = list(ctx.selected_objects)
    elif scope == 'scene':
        objects = list(ctx.scene.objects)
    else:
        coll = bpy.data.collections.get(collection)
        if coll is None:
            raise ValueError('Collection does not exist: ' + collection)
        objects = list(coll.all_objects)
    names = {o.name for o in objects if not o.hide_render}
    totals = dict(vertices=0, edges=0, faces=0, triangles=0)
    count = 0
    def accumulate(mesh):
        nonlocal count
        mesh.calc_loop_triangles()
        totals['vertices'] += len(mesh.vertices)
        totals['edges'] += len(mesh.edges)
        totals['faces'] += len(mesh.polygons)
        totals['triangles'] += len(mesh.loop_triangles)
        count += 1
    if evaluated:
        graph = ctx.evaluated_depsgraph_get()
        for instance in graph.object_instances:
            obj = instance.object
            source = obj.original
            parent = instance.parent.original if instance.parent else None
            if source.name not in names and not (parent and parent.name in names):
                continue
            if source.hide_render or obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
                continue
            mesh = obj.to_mesh()
            try:
                if mesh is not None:
                    accumulate(mesh)
            finally:
                obj.to_mesh_clear()
    else:
        for obj in objects:
            if obj.name in names and obj.type == 'MESH':
                accumulate(obj.data)
    return {**totals, 'mesh_instances': count, 'evaluated': evaluated,
            'evaluation': 'viewport depsgraph; render-hidden objects excluded; render-only modifier levels may differ'}


def _png(width, height, rgba):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    stride = width * 4
    rows = b''.join(b'\0' + rgba[y * stride:(y + 1) * stride] for y in range(height - 1, -1, -1))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def capture_viewport(ctx, max_size=768):
    import gpu
    window, area, region = _viewport(ctx)
    scale = min(1, max_size / max(region.width, region.height))
    width, height = max(1, int(region.width * scale)), max(1, int(region.height * scale))
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            rv = region.data
            offscreen.draw_view3d(ctx.scene, ctx.view_layer, area.spaces.active, region,
                                 rv.view_matrix, rv.window_matrix, do_color_management=True)
            with offscreen.bind():
                pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE')
                pixels.dimensions = width * height * 4
                image = _png(width, height, bytes(pixels))
    finally:
        offscreen.free()
    return {'mime_type': 'image/png', 'width': width, 'height': height,
            'shading': area.spaces.active.shading.type, 'data': base64.b64encode(image).decode('ascii')}


class _Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.remaining = 12000

    def write(self, text):
        size = len(text)
        super().write(text[:self.remaining])
        self.remaining = max(0, self.remaining - size)
        return size


def run_bpy(ctx, source):
    # Compile before making a checkpoint; do not pretend bpy is sandboxed.
    code = compile(source, '<AI Copilot>', 'exec')
    if not bpy.context.preferences.edit.use_global_undo:
        raise RuntimeError('Enable Blender Global Undo before running Copilot Python')
    if not bpy.ops.ed.undo_push.poll():
        raise RuntimeError('Blender undo checkpoint is unavailable in this context')
    bpy.ops.ed.undo_push(message='Before AI Copilot Python')
    output = _Output()
    result = {'success': True}
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(code, {'__name__': '__copilot__', 'bpy': bpy})
    except BaseException as ex:
        result.update(success=False, error=str(ex)[:2000], traceback=traceback.format_exc()[-6000:])
    finally:
        # A completed snapshot is also needed: Undo restores the pre-action state.
        # Preserve undo for partially executed code that subsequently raises.
        if bpy.ops.ed.undo_push.poll():
            bpy.ops.ed.undo_push(message='AI Copilot Python')
    result['stdout'] = output.getvalue()
    return result


def undo(ctx):
    if not bpy.ops.ed.undo.poll():
        raise RuntimeError('Blender Undo is unavailable')
    bpy.ops.ed.undo()
    return {'success': True}


def make_registry():
    registry = BlenderToolRegistry()
    def schema(props=None, required=()):
        return {'type': 'object', 'properties': props or {}, 'required': list(required), 'additionalProperties': False}
    def output(props):
        return {'type': 'object', 'properties': {'ok': {'type': 'boolean'}, 'error': {'type': 'string'},
                'traceback': {'type': 'string'}, **props}, 'required': ['ok']}
    string = {'type': 'string'}
    nullable_string = {'type': ['string', 'null']}
    vector = {'type': 'array', 'items': {'type': 'number'}}
    names = {'type': 'array', 'items': string}
    counts = {k: {'type': 'integer'} for k in ('vertices', 'edges', 'faces', 'triangles')}
    specifications = [
        ('inspect_scene', 'Inspect compact scene facts. Use offset/limit for more objects; do not guess names.',
         schema({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}}),
         output({'scene': string, 'mode': string, 'active_object': nullable_string,
                 'selected_objects': names, 'objects': {'type': 'array', 'items': {'type': 'object'}},
                 'units': {'type': 'object'}, 'collections': {'type': 'array', 'items': {'type': 'object'}},
                 'viewport': {'type': ['object', 'null']}, 'object_count': {'type': 'integer'},
                 'offset': {'type': 'integer'}, 'truncated': {'type': 'boolean'}}), inspect_scene, False),
        ('inspect_object', 'Inspect a named current-scene object, transforms, bounds, hierarchy, modifiers and materials.',
         schema({'name': string}, ('name',)),
         output({'name': string, 'type': string, 'location': vector, 'rotation_euler': vector,
                 'rotation_quaternion': vector, 'rotation_mode': string, 'scale': vector, 'dimensions': vector,
                 'matrix_world': {'type': 'array', 'items': vector},
                 'bounding_box_world': {'type': 'array', 'items': vector}, 'parent': nullable_string,
                 'children': names, 'modifiers': {'type': 'array', 'items': {'type': 'object'}},
                 'materials': {'type': 'array', 'items': nullable_string}, 'mesh': {'type': 'object'}}), inspect_object, False),
        ('capture_viewport', 'Capture current 3D Viewport shading/view as a small PNG visual input.',
         schema({'max_size': {'type': 'integer', 'minimum': 64, 'maximum': 1024}}),
         output({'mime_type': string, 'data': string, 'width': {'type': 'integer'}, 'height': {'type': 'integer'},
                 'shading': string}), capture_viewport, False),
        ('mesh_stats', 'Count mesh vertices/edges/faces/triangles, normally evaluated after visible modifiers and instancing.',
         schema({'scope': {'type': 'string', 'enum': ['object', 'selected', 'scene', 'collection']},
                 'name': string, 'collection': string, 'evaluated': {'type': 'boolean'}}),
         output({**counts, 'mesh_instances': {'type': 'integer'}, 'evaluated': {'type': 'boolean'},
                 'evaluation': string}), mesh_stats, False),
        ('run_bpy', 'Execute raw Blender Python on the main thread with undo checkpoints, stdout and exception details.',
         schema({'source': {'type': 'string', 'maxLength': 200000}}, ('source',)),
         output({'success': {'type': 'boolean'}, 'stdout': string}), run_bpy, True),
        ('undo', 'Use Blender Undo to restore the preceding checkpoint.', schema(),
         output({'success': {'type': 'boolean'}}), undo, True),
    ]
    for args in specifications:
        registry.add(BlenderTool(*args))
    return registry
