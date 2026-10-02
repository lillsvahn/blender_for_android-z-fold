# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Built into the APK's startup scripts; no add-on installation required."""
import base64
import json
import os
import sys
import tempfile
import time
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bl_android_copilot.runtime import job, platform_call
import bl_android_navigation as navigation

_loading = False
_key_configured = False
_key_checked = 0.0
_config_error = ''
_saved_fields = ('mobile_navigation', 'movement_speed', 'look_sensitivity', 'joystick_size',
                 'joystick_opacity', 'provider', 'model', 'max_iterations')


def _config_path():
    return os.path.join(bpy.utils.user_resource('CONFIG', create=True), 'zfold_v0_1.json')


def _save(self, context):
    global _config_error
    if _loading:
        return
    try:
        path = _config_path()
        with open(path + '.tmp', 'w', encoding='utf8') as file:
            json.dump({key: getattr(self, key) for key in _saved_fields}, file)
        os.replace(path + '.tmp', path)
        _config_error = ''
    except Exception as ex:
        _config_error = 'Settings could not be saved: ' + str(ex)[:180]
    if context and context.window:
        for area in context.window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _load():
    global _loading, _config_error
    _loading = True
    try:
        path = _config_path()
        if os.path.isfile(path):
            with open(path, encoding='utf8') as file:
                data = json.load(file)
            settings = bpy.context.window_manager.zfold
            for key in _saved_fields:
                if key in data:
                    setattr(settings, key, data[key])
    except Exception as ex:
        _config_error = 'Settings could not be loaded: ' + str(ex)[:180]
    finally:
        _loading = False


class ZFOLD_Settings(bpy.types.PropertyGroup):
    mobile_navigation: BoolProperty(name='Mobile Navigation', default=False, update=_save)
    movement_speed: FloatProperty(name='Movement Speed', description='Blender units per second',
                                  default=3, min=0.01, max=1000, update=_save)
    look_sensitivity: FloatProperty(name='Look Sensitivity', description='Radians per second at full stick',
                                    default=1.6, min=0.05, max=10, update=_save)
    joystick_size: FloatProperty(name='Joystick Size', description='Diameter in UI pixels',
                                default=140, min=70, max=300, update=_save)
    joystick_opacity: FloatProperty(name='Joystick Opacity', default=0.45, min=0.05, max=1, update=_save)
    provider: EnumProperty(name='Provider', items=[('Gemini', 'Gemini', 'Google AI Studio API')], update=_save)
    model: StringProperty(name='Model', description='Gemini model ID from Google AI Studio', maxlen=128, update=_save)
    goal: StringProperty(name='Goal', description='Describe the modeling task and triangle budget', maxlen=12000)
    reference_path: StringProperty(name='Reference Image', subtype='FILE_PATH')
    max_iterations: IntProperty(name='Max Iterations', default=20, min=1, max=100, update=_save)


def _reference(path):
    if not path:
        return None
    path = bpy.path.abspath(path)
    if not os.path.isfile(path) or os.path.getsize(path) > 16 * 1024 * 1024:
        raise ValueError('Select one PNG/JPEG/WebP image smaller than 16 MiB')
    if os.path.splitext(path)[1].lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        raise ValueError('Reference must be PNG, JPEG or WebP')
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        if width < 1 or height < 1:
            raise ValueError('Reference image could not be decoded')
        scale = min(1, 1024 / max(width, height))
        if scale < 1:
            image.scale(max(1, int(width * scale)), max(1, int(height * scale)))
        with tempfile.TemporaryDirectory(prefix='zfold-reference-') as folder:
            image.filepath_raw = os.path.join(folder, 'reference.png')
            image.file_format = 'PNG'
            image.save()
            with open(image.filepath_raw, 'rb') as file:
                data = file.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError('Converted reference image exceeds 4 MiB')
            return {'mime_type': 'image/png', 'data': base64.b64encode(data).decode('ascii')}
    finally:
        bpy.data.images.remove(image)


class ZFOLD_OT_key(bpy.types.Operator):
    bl_idname = 'zfold.copilot_key'
    bl_label = 'Set / Clear API Key'
    def execute(self, context):
        reply = platform_call('configure_key')
        if not reply.get('ok'):
            self.report({'ERROR'}, reply.get('error', 'Key dialog unavailable'))
            return {'CANCELLED'}
        return {'FINISHED'}


class ZFOLD_OT_reference(bpy.types.Operator):
    bl_idname = 'zfold.copilot_reference'
    bl_label = 'Select Reference Image'
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.png;*.jpg;*.jpeg;*.webp', options={'HIDDEN'})
    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    def execute(self, context):
        context.window_manager.zfold.reference_path = self.filepath
        return {'FINISHED'}


class ZFOLD_OT_clear_reference(bpy.types.Operator):
    bl_idname = 'zfold.copilot_clear_reference'
    bl_label = 'Clear Reference'
    def execute(self, context):
        context.window_manager.zfold.reference_path = ''
        return {'FINISHED'}


class ZFOLD_OT_action(bpy.types.Operator):
    bl_idname = 'zfold.copilot_action'
    bl_label = 'Copilot Action'
    action: EnumProperty(items=[(x, x, '') for x in ('Run', 'Continue', 'Undo', 'Stop')])
    def execute(self, context):
        try:
            settings = context.window_manager.zfold
            if self.action == 'Run':
                job.run(settings, _reference(settings.reference_path))
            elif self.action == 'Continue':
                job.advance()
            elif self.action == 'Undo':
                job.undo()
            else:
                job.stop()
            return {'FINISHED'}
        except Exception as ex:
            self.report({'ERROR'}, str(ex)[:500])
            return {'CANCELLED'}


class ZFOLD_OT_log(bpy.types.Operator):
    bl_idname = 'zfold.copilot_log'
    bl_label = 'Review Copilot Log'
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=500)
    def draw(self, context):
        self.layout.label(text='Full Python/output: Text Editor > AI_Copilot_Log')
        for line in job.log_buffer.splitlines()[-20:]:
            self.layout.label(text=line[:100])
    def execute(self, context):
        return {'FINISHED'}


class ZFOLD_PT_navigation(bpy.types.Panel):
    bl_label = 'Mobile Navigation'
    bl_idname = 'ZFOLD_PT_navigation'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Z Fold'
    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.zfold
        layout.prop(settings, 'mobile_navigation')
        column = layout.column()
        column.active = settings.mobile_navigation
        for name in ('movement_speed', 'look_sensitivity', 'joystick_size', 'joystick_opacity'):
            column.prop(settings, name)
        if _config_error:
            layout.label(text=_config_error, icon='ERROR')
        if navigation._error:
            layout.label(text=navigation._error[:70], icon='ERROR')


class ZFOLD_PT_copilot(bpy.types.Panel):
    bl_label = 'AI Copilot'
    bl_idname = 'ZFOLD_PT_copilot'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Z Fold'
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.zfold
        column = layout.column()
        column.enabled = not job.pending
        column.prop(settings, 'provider')
        column.prop(settings, 'model')
        column.label(text='API Key: ' + ('configured' if _key_configured else 'not configured'))
        column.operator('zfold.copilot_key')
        column.prop(settings, 'goal')
        column.label(text='Reference: ' + (os.path.basename(settings.reference_path) if settings.reference_path else 'none'))
        row = column.row(align=True)
        row.operator('zfold.copilot_reference', text='Select')
        row.operator('zfold.copilot_clear_reference', text='Clear')
        column.prop(settings, 'max_iterations')
        row = layout.row(align=True)
        cell = row.row(); cell.enabled = not job.pending
        cell.operator('zfold.copilot_action', text='Run').action = 'Run'
        cell = row.row(); cell.enabled = job.active and not job.pending and job.iteration < job.maximum
        cell.operator('zfold.copilot_action', text='Continue').action = 'Continue'
        row = layout.row(align=True)
        cell = row.row(); cell.enabled = job.target is not None
        cell.operator('zfold.copilot_action', text='Undo').action = 'Undo'
        row.operator('zfold.copilot_action', text='Stop').action = 'Stop'
        layout.label(text=f'Iteration {job.iteration} / {job.maximum}')
        layout.label(text='API: ' + job.api_status)
        layout.label(text='Last: ' + job.last_action)
        if job.triangles is not None:
            layout.label(text=f'Scene triangles: {job.triangles:,}')
        import textwrap
        for line in textwrap.wrap(job.status, 38)[:5]:
            layout.label(text=line)
        layout.operator('zfold.copilot_log')


def _timer():
    global _key_configured, _key_checked
    try:
        before = (job.status, job.api_status, job.last_action, job.iteration, job.pending, job.triangles, _key_configured)
        navigation.tick(bpy.context.window_manager.zfold)
        job.poll()
        if time.monotonic() - _key_checked > 2:
            _key_configured = platform_call('status').get('configured', False)
            _key_checked = time.monotonic()
        after = (job.status, job.api_status, job.last_action, job.iteration, job.pending, job.triangles, _key_configured)
        if before != after:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
    except Exception as ex:
        job.status = 'Android runtime: ' + str(ex)[:200]
        # Release axes on failure; never leave a held stick moving unattended.
        navigation.clear()
    return 1 / 30


def _initial_load():
    # register() is called under RestrictBlend; defer real context/config access.
    _load()
    return None


@persistent
def _load_pre(_):
    job.stop()
    navigation.clear()


@persistent
def _load_post(_):
    _load()


classes = (ZFOLD_Settings, ZFOLD_OT_key, ZFOLD_OT_reference, ZFOLD_OT_clear_reference,
           ZFOLD_OT_action, ZFOLD_OT_log, ZFOLD_PT_navigation, ZFOLD_PT_copilot)


def register():
    if not hasattr(sys, 'getandroidapilevel'):
        return
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.zfold = PointerProperty(type=ZFOLD_Settings)
    navigation.register()
    bpy.app.handlers.load_pre.append(_load_pre)
    bpy.app.handlers.load_post.append(_load_post)
    bpy.app.timers.register(_initial_load, first_interval=0.2)
    bpy.app.timers.register(_timer, first_interval=0.3, persistent=True)


def unregister():
    if not hasattr(bpy.types.WindowManager, 'zfold'):
        return
    job.stop()
    for timer in (_timer, _initial_load):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for handlers, callback in ((bpy.app.handlers.load_pre, _load_pre), (bpy.app.handlers.load_post, _load_post)):
        if callback in handlers:
            handlers.remove(callback)
    navigation.unregister()
    del bpy.types.WindowManager.zfold
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
