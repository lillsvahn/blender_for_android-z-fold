# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Android touch overlay. GHOST owns fingers; RegionView3D owns spatial motion."""
import math
import time
import bpy
import _bpy
from mathutils import Vector

_handle = None
_regions = {}
_axes = {}
_last = 0.0
_error = ""


def geometry(area, region, size):
    """Avoid overlapping Sidebar/Tools. Draw and hit-test use identical pixels."""
    left, right = 0.0, float(region.width)
    for other in area.regions:
        if other.type not in {'UI', 'TOOLS'} or other.width < 2:
            continue
        if other.x < region.x + region.width / 2:
            left = max(left, float(other.x + other.width - region.x))
        else:
            right = min(right, float(other.x - region.x))
    radius = min(size * 0.5, (right - left - 48) / 5.5, (region.height - 48) / 3)
    if radius < 24:
        return None
    y = radius + 22
    return (left, right, left + radius + 20, y, right - radius - 20, y,
            radius, (left + right) / 2, y)


def navigate(rv, axes, dt, speed, sensitivity):
    mx, my, lx, ly = axes
    if not any(axes):
        return
    distance = rv.view_distance
    rotation = rv.view_rotation.copy()
    eye = rv.view_location + rotation @ Vector((0, 0, distance))
    # Translating the eye/target together changes neither distance nor lens/FOV.
    eye += rotation @ Vector((mx * speed * dt, 0, -my * speed * dt))
    forward = rotation @ Vector((0, 0, -1))
    yaw = math.atan2(forward.y, forward.x) - lx * sensitivity * dt
    pitch = math.asin(max(-1, min(1, forward.z))) + ly * sensitivity * dt
    pitch = max(-math.radians(89), min(math.radians(89), pitch))
    forward = Vector((math.cos(pitch) * math.cos(yaw),
                      math.cos(pitch) * math.sin(yaw), math.sin(pitch)))
    rotation = forward.to_track_quat('-Z', 'Y')  # World Z is up: no roll.
    rv.view_perspective = 'PERSP'  # Do not move a real scene camera.
    rv.view_rotation = rotation
    rv.view_location = eye - rotation @ Vector((0, 0, distance))


def tick(settings):
    global _last, _regions, _axes, _error
    now = time.monotonic()
    dt = min(0.04, max(0, now - _last)) if _last else 0
    _last = now
    layouts, regions = [], {}
    if settings.mobile_navigation:
        scale = bpy.context.preferences.system.ui_scale
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for region in area.regions:
                    if region.type != 'WINDOW' or not region.data:
                        continue
                    geom = geometry(area, region, settings.joystick_size * scale)
                    if geom is None or len(layouts) >= 32:
                        continue
                    left, right, mx, my, lx, ly, radius, fx, fy = geom
                    rid = len(layouts) + 1
                    top = window.height
                    layouts.append((rid, region.x + left, top - region.y - region.height,
                                    region.x + right, top - region.y,
                                    region.x + mx, top - region.y - my,
                                    region.x + lx, top - region.y - ly, radius,
                                    region.x + fx, top - region.y - fy))
                    regions[rid] = (window, area, region, geom)
    _bpy.android_mobile_layout(layouts)
    previous_regions, previous_axes = _regions, _axes
    _regions = regions
    _axes = {}
    if previous_regions != regions:
        # Clear already drawn controls when disabling/changing an editor too.
        for _, area, _, _ in previous_regions.values():
            try:
                area.tag_redraw()
            except ReferenceError:
                pass  # A removed screen/area no longer has a drawable surface.
        for _, area, _, _ in regions.values():
            area.tag_redraw()
    for rid, mx, my, lx, ly, frame in _bpy.android_mobile_state():
        if rid not in regions:
            continue
        window, area, region, geom = regions[rid]
        _axes[region.as_pointer()] = (mx, my, lx, ly)
        try:
            navigate(region.data, (mx, my, lx, ly), dt,
                     settings.movement_speed, settings.look_sensitivity)
            if frame:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_selected('EXEC_DEFAULT', use_all_regions=False)
            if (frame or any((mx, my, lx, ly)) or
                    previous_axes.get(region.as_pointer(), (0, 0, 0, 0)) != (mx, my, lx, ly)):
                area.tag_redraw()
        except Exception as ex:
            _error = str(ex)


def _draw():
    import gpu
    import blf
    from gpu_extras.batch import batch_for_shader
    region = bpy.context.region
    wm = bpy.context.window_manager
    if not region or not hasattr(wm, 'zfold') or not wm.zfold.mobile_navigation:
        return
    match = next((item for item in _regions.values() if item[2] == region), None)
    if match is None:
        return
    geom = match[3]
    _, _, mx, my, lx, ly, radius, fx, fy = geom
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    opacity = wm.zfold.joystick_opacity
    def circle(x, y, r, color):
        vertices = []
        for i in range(48):
            a, b = i * math.tau / 48, (i + 1) * math.tau / 48
            vertices.extend(((x, y), (x + math.cos(a) * r, y + math.sin(a) * r),
                             (x + math.cos(b) * r, y + math.sin(b) * r)))
        shader.bind()
        shader.uniform_float('color', color)
        batch_for_shader(shader, 'TRIS', {'pos': vertices}).draw(shader)
    gpu.state.blend_set('ALPHA')
    try:
        values = _axes.get(region.as_pointer(), (0, 0, 0, 0))
        for i, (x, y, label) in enumerate(((mx, my, 'MOVE'), (lx, ly, 'LOOK'))):
            circle(x, y, radius, (0.08, 0.1, 0.13, opacity))
            circle(x + values[i * 2] * radius * 0.65,
                   y + values[i * 2 + 1] * radius * 0.65,
                   radius * 0.3, (0.65, 0.75, 0.85, opacity))
            blf.size(0, 12 * bpy.context.preferences.system.ui_scale)
            width, _ = blf.dimensions(0, label)
            blf.color(0, 1, 1, 1, max(0.6, opacity))
            blf.position(0, x - width / 2, y - radius + 10, 0)
            blf.draw(0, label)
        circle(fx, fy, radius * 0.48, (0.15, 0.2, 0.25, opacity))
        width, _ = blf.dimensions(0, 'FRAME')
        blf.position(0, fx - width / 2, fy - 5, 0)
        blf.draw(0, 'FRAME')
    finally:
        gpu.state.blend_set('NONE')


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def clear():
    global _regions, _axes, _last
    _bpy.android_mobile_layout([])
    _regions, _axes, _last = {}, {}, 0.0


def unregister():
    global _handle
    clear()
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
