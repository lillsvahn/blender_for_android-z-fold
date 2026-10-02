# SPDX-License-Identifier: GPL-2.0-or-later
"""No Blender/SDK/network required. Real bpy tests are in test_blender.py."""
import json
import pathlib
import struct
import sys
import threading
import types
import unittest
import zlib
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / 'scripts/modules'))
bpy = types.ModuleType('bpy')
bpy.context = types.SimpleNamespace()
sys.modules['bpy'] = bpy
sys.modules['_bpy'] = types.ModuleType('_bpy')
from bl_android_copilot.registry import make_registry, _png
from bl_android_copilot.gemini import GeminiAdapter
from bl_android_copilot.runtime import CopilotJob


class Tests(unittest.TestCase):
    def setUp(self):
        self.registry = make_registry()
        bpy.ops = types.SimpleNamespace(ed=types.SimpleNamespace(undo_push=Mock(), undo=Mock()))
        bpy.ops.ed.undo_push.poll.return_value = True

    def test_registry_contract(self):
        self.assertEqual(set(self.registry.tools), {'inspect_scene', 'inspect_object', 'capture_viewport', 'mesh_stats', 'run_bpy', 'undo'})
        for tool in self.registry.tools.values():
            self.assertTrue(tool.description)
            self.assertEqual(tool.input_schema['type'], 'object')
            self.assertEqual(tool.output_schema['type'], 'object')
        self.assertFalse(self.registry.execute('unknown', {})['ok'])
        self.assertFalse(self.registry.execute('run_bpy', {})['ok'])
        self.assertFalse(self.registry.execute('mesh_stats', {'scope': 'bad'})['ok'])
        self.assertFalse(self.registry.execute('inspect_scene', {'limit': 101})['ok'])

    def test_thread_guard(self):
        results = []
        t = threading.Thread(target=lambda: results.append(self.registry.execute('undo', {})))
        t.start(); t.join()
        self.assertIn('main thread', results[0]['error'])

    def test_python_output_exception_undo(self):
        bpy.ops = types.SimpleNamespace(ed=types.SimpleNamespace(undo_push=Mock(), undo=Mock()))
        bpy.ops.ed.undo_push.poll.return_value = True
        result = self.registry.execute('run_bpy', {'source': "print('created'); raise ValueError('broken')"})
        self.assertTrue(result['ok'])
        self.assertFalse(result['success'])
        self.assertEqual(result['stdout'], 'created\n')
        self.assertIn('ValueError: broken', result['traceback'])
        self.assertEqual(bpy.ops.ed.undo_push.call_count, 2)
        self.registry.execute('run_bpy', {'source': 'invalid syntax !'})
        self.assertEqual(bpy.ops.ed.undo_push.call_count, 2)  # Compile before checkpoint.

    def test_output_bound(self):
        result = self.registry.execute('run_bpy', {'source': "print('x'*100000)"})
        self.assertLessEqual(len(result['stdout']), 12000)

    def test_wire_images_signatures(self):
        transport = Mock(return_value={'ok': True})
        adapter = GeminiAdapter(self.registry, transport)
        previous = {'role': 'model', 'parts': [{'functionCall': {'name': 'undo', 'args': {}, 'id': 'id1'}, 'thoughtSignature': 'opaque'}]}
        responses = [{'functionResponse': {'name': 'undo', 'id': 'id1', 'response': {'ok': True}}}]
        image = {'mime_type': 'image/png', 'data': 'abc'}
        adapter.start('configurable-flash', 'tree', {}, image, image, [], previous, responses)
        action, request = transport.call_args.args
        self.assertEqual(action, 'request')
        self.assertEqual(request['body']['contents'][0], previous)
        self.assertEqual(request['body']['contents'][1]['parts'], responses)
        self.assertEqual(sum('inlineData' in p for p in request['body']['contents'][-1]['parts']), 2)
        self.assertNotIn('api_key', json.dumps(request))
        undo = next(t for t in request['body']['tools'][0]['functionDeclarations'] if t['name'] == 'undo')
        self.assertNotIn('parameters', undo)

    def test_incomplete_never_executes(self):
        with self.assertRaises(ValueError):
            GeminiAdapter.parse({'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': []}}]})

    def job(self, response=None):
        transport = Mock()
        transport.side_effect = lambda action, payload=None: (
            response if action == 'poll' and response is not None else {'ok': True, 'pending': True})
        job = CopilotJob(transport=transport, registry=self.registry)
        job.active = True; job.model = 'flash'; job.goal = 'tree'
        job._context = lambda: __import__('contextlib').nullcontext()
        job._refresh = Mock()
        job.log = Mock(); job.restore_log = Mock()
        self.registry.execute = Mock(return_value={'ok': True})
        return job, transport

    def test_one_request_per_continue_and_max(self):
        job, transport = self.job({'ok': True, 'response': {'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': [{'text': 'Done'}]}}]}})
        job.maximum = 2
        job.advance(); job.advance()
        self.assertEqual(sum(c.args[0] == 'request' for c in transport.call_args_list), 1)
        job.poll()
        for _ in range(20): job.poll()
        self.assertEqual(sum(c.args[0] == 'request' for c in transport.call_args_list), 1)
        job.advance(); job.poll(); job.advance()
        self.assertEqual(job.iteration, 2)
        self.assertFalse(job.active)

    def test_mutation_batch_refused(self):
        parts = [{'functionCall': {'name': name, 'args': args}} for name, args in [('run_bpy', {'source': 'print(1)'}), ('undo', {})]]
        job, _ = self.job({'ok': True, 'response': {'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': parts}}]}})
        job.pending = True
        job.poll()
        self.registry.execute.assert_not_called()
        self.assertIn('one mutating', job.status)

    def test_429_no_retry(self):
        job, transport = self.job({'ok': False, 'http_status': 429, 'error': 'rate limit', 'retry_after': 120})
        job.advance(); job.poll()
        for _ in range(50): job.poll(); job.advance()
        self.assertEqual(sum(c.args[0] == 'request' for c in transport.call_args_list), 1)

    def test_stop_late_response(self):
        job, transport = self.job()
        job.advance(); job.stop(); job.poll()
        self.assertFalse(job.active)
        self.assertFalse(job.pending)
        self.assertEqual(sum(c.args[0] == 'poll' for c in transport.call_args_list), 0)

    def test_png_top_row(self):
        png = _png(1, 2, bytes((255, 0, 0, 255, 0, 0, 255, 255)))
        i = 8; data = b''
        while i < len(png):
            size = struct.unpack('!I', png[i:i + 4])[0]
            if png[i + 4:i + 8] == b'IDAT': data += png[i + 8:i + 8 + size]
            i += size + 12
        self.assertEqual(zlib.decompress(data), bytes((0, 0, 0, 255, 255, 0, 255, 0, 0, 255)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
