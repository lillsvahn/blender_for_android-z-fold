# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later
"""User-controlled orchestration. Timer polling never initiates an API request."""
import json
import time
import bpy
import _bpy
from .registry import make_registry, _viewport
from .gemini import GeminiAdapter


def platform_call(action, payload=None):
    return json.loads(_bpy.android_copilot(action, json.dumps(payload or {}, ensure_ascii=True)))


class CopilotJob:
    def __init__(self, transport=platform_call, registry=None):
        self.transport = transport
        self.registry = registry or make_registry()
        self.provider = GeminiAdapter(self.registry, transport)
        self.iteration = 0
        self.maximum = 20
        self.pending = False
        self.active = False
        self.status = 'Ready'
        self.api_status = 'Idle'
        self.last_action = 'None'
        self.triangles = None
        self.goal = self.model = ''
        self.reference = self.viewport = None
        self.previous = None
        self.responses = []
        self.memory = []
        self.cooldown = 0
        self.target = None
        self.log_buffer = ''

    def log(self, text):
        self.log_buffer += text + '\n'
        if len(self.log_buffer) > 300000:
            self.log_buffer = self.log_buffer[-250000:]
        self.restore_log()

    def restore_log(self):
        if self.log_buffer:
            # Undo can roll back datablocks. Keep the session log outside undo
            # state and restore the visible Text after an undo or mutation.
            log = bpy.data.texts.get('AI_Copilot_Log') or bpy.data.texts.new('AI_Copilot_Log')
            log.clear()
            log.write(self.log_buffer)

    def _context(self):
        if self.target:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    for region in area.regions:
                        if (area.as_pointer(), region.as_pointer()) == self.target and area.type == 'VIEW_3D':
                            return bpy.context.temp_override(window=window, area=area, region=region)
        raise ValueError('The job\'s 3D Viewport was closed/changed; start a new Run')

    def run(self, settings, reference=None):
        self.stop()
        if not settings.goal.strip() or not settings.model.strip():
            raise ValueError('Set a goal and Gemini model ID first')
        _, area, region = _viewport(bpy.context)
        self.target = (area.as_pointer(), region.as_pointer())
        self.goal, self.model = settings.goal.strip(), settings.model.strip()
        self.maximum = settings.max_iterations
        self.iteration = 0
        self.memory, self.responses, self.previous = [], [], None
        self.reference = reference
        self.active = True
        self.log('Run: ' + self.goal)
        self.advance()

    def _refresh(self):
        stats = self.registry.execute('mesh_stats', {'scope': 'scene'})
        self.triangles = stats.get('triangles') if stats.get('ok') else None
        capture = self.registry.execute('capture_viewport', {})
        self.viewport = capture if capture.get('ok') else None
        if not capture.get('ok'):
            self.log('Viewport capture: ' + capture.get('error', 'unavailable'))

    def advance(self):
        if not self.active or self.pending:
            return
        if self.iteration >= self.maximum:
            self.active = False
            self.status = 'Max Iterations reached'
            return
        if time.monotonic() < self.cooldown:
            self.status = 'Rate limit: wait before Continue'
            return
        try:
            with self._context():
                self._refresh()
                facts = self.registry.execute('inspect_scene', {})
            reply = self.provider.start(self.model, self.goal, facts, self.viewport, self.reference,
                                        self.memory, self.previous, self.responses)
            if not reply.get('ok'):
                self._error(reply)
                return
            self.iteration += 1
            self.pending = True
            self.status = 'Waiting for Gemini'
            self.api_status = 'Request running'
        except Exception as ex:
            self.status = str(ex)[:500]
            self.log(self.status)

    def _error(self, reply):
        self.pending = False
        self.status = reply.get('error', 'API failed')[:500]
        self.api_status = 'HTTP ' + str(reply.get('http_status', 'network error'))
        if reply.get('http_status') == 429:
            self.cooldown = time.monotonic() + max(1, reply.get('retry_after', 60))
        self.log(self.api_status + ': ' + self.status)

    def poll(self):
        if not self.pending:
            return
        try:
            reply = self.transport('poll')
            if reply.get('pending'):
                return
            # Pause/Stop cancels in Java and empties its result. Never run late
            # Python when no response belongs to the pending request.
            if not reply.get('ok') or 'response' not in reply:
                self._error(reply if not reply.get('ok') else {'error': 'Request cancelled by Android lifecycle'})
                return
            self.pending = False
            self.api_status = 'HTTP ' + str(reply.get('http_status', 200))
            content, calls, report = self.provider.parse(reply['response'])
            self.previous = content  # Includes thought signatures verbatim.
            self.responses = []
            refusal = None
            try:
                for call in calls:
                    self.registry.validate(call['name'], call.get('args', {}))
                if sum(self.registry.tools[c['name']].mutating for c in calls) > 1:
                    raise ValueError('Only one mutating tool per user iteration')
            except Exception as ex:
                refusal = str(ex)
            if report:
                self.log(report)
            with self._context():
                summaries = []
                for call in calls:
                    name, inputs = call['name'], call.get('args', {})
                    if name == 'run_bpy':
                        self.log('Generated Python:\n' + inputs.get('source', ''))
                    result = ({'ok': False, 'error': refusal} if refusal else
                              self.registry.execute(name, inputs))
                    if name == 'capture_viewport' and result.get('ok'):
                        self.viewport = result
                    # Image bytes become inlineData visual input, never a huge
                    # JSON string in the function-response text or UI log.
                    compact = {k: v for k, v in result.items() if k != 'data'}
                    self.last_action = name + (': complete' if result.get('ok') and result.get('success', True) else ': failed; review log')
                    self.log(name + ': ' + json.dumps(compact, ensure_ascii=False)[:14000])
                    wire = {'name': name, 'response': compact}
                    if 'id' in call:
                        wire['id'] = call['id']
                    self.responses.append({'functionResponse': wire})
                    summaries.append(name + ': ' + json.dumps(compact, ensure_ascii=False)[:1000])
                    if not refusal and self.registry.tools[name].mutating:
                        self.restore_log()
                self._refresh()
            self.memory.append({'iteration': self.iteration, 'report': report[:1000], 'tools': summaries})
            self.memory = self.memory[-4:]
            if self.iteration >= self.maximum:
                self.active = False
                self.status = 'Max Iterations reached'
            else:
                self.status = refusal or 'Review result, then Continue'
        except Exception as ex:
            self.pending = False
            self.status = str(ex)[:500]
            self.log('Copilot error: ' + self.status)

    def stop(self):
        self.transport('stop')
        self.pending = self.active = False
        self.status = 'Stopped'

    def undo(self):
        if self.pending:
            self.stop()
        with self._context():
            result = self.registry.execute('undo', {})
            self.restore_log()
            self._refresh()
        # Results of a now-undone action are stale; the next request gets facts.
        self.previous, self.responses = None, []
        self.log('User Undo: ' + json.dumps(result))
        self.status = 'Undo complete' if result.get('ok') else result.get('error', 'Undo failed')


job = CopilotJob()
