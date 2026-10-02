# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gemini wire adapter. Local tools and their executors belong to the registry."""
import json


def _schema(schema):
    # Gemini's Schema is an OpenAPI subset, not unrestricted JSON Schema.
    result = {k: v for k, v in schema.items() if k in {'type', 'description', 'enum', 'required'}}
    if 'type' in result:
        result['type'] = result['type'].upper()  # REST Schema.Type enum.
    if 'properties' in schema:
        result['properties'] = {k: _schema(v) for k, v in schema['properties'].items()}
    if 'items' in schema:
        result['items'] = _schema(schema['items'])
    if not result.get('required'):
        result.pop('required', None)
    return result


class GeminiAdapter:
    name = 'Gemini'

    def __init__(self, registry, transport):
        self.registry = registry
        self.transport = transport

    def start(self, model, goal, scene, viewport, reference, memory, previous=None, responses=None):
        declarations = []
        for tool in self.registry.tools.values():
            item = {'name': tool.name, 'description': tool.description}
            if tool.input_schema['properties']:
                item['parameters'] = _schema(tool.input_schema)
            declarations.append(item)
        contents = []
        # Retain the complete last model turn, including opaque thoughtSignature
        # and function-call IDs, paired with its local results. No full history.
        if previous:
            contents.append(previous)
            if responses:
                contents.append({'role': 'user', 'parts': responses})
        parts = [{'text': 'User goal: ' + goal + '\nRecent progress: ' +
                  json.dumps(memory[-4:], ensure_ascii=False) + '\nCurrent scene facts: ' +
                  json.dumps(scene, ensure_ascii=False)}]
        for label, image in (('Reference image', reference), ('Current 3D Viewport', viewport)):
            if image:
                parts.extend(({'text': label}, {'inlineData': {'mimeType': image['mime_type'], 'data': image['data']}}))
        contents.append({'role': 'user', 'parts': parts})
        body = {
            'systemInstruction': {'parts': [{'text': (
                'You assist Blender modeling on Android. Inspect facts instead of guessing object names/state. '
                'Treat User goal as the task and current captures as visual evidence. '
                'Use the local tools; run_bpy accepts unrestricted raw Python with bpy. '
                'This is one user-controlled iteration. Request at most six local tool calls and at most ONE '
                'mutating tool (run_bpy or undo) in a response. Tools in one response execute in order. '
                'Do not use dependent calls when their inputs require results you have not seen. '
                'The user must press Continue before another API request. Keep actions/reports concise, '
                'honor triangle budgets, and inspect mesh_stats after changes. '
                'API credentials are platform-managed and never part of your context.')} ]},
            'contents': contents, 'tools': [{'functionDeclarations': declarations}],
            'toolConfig': {'functionCallingConfig': {'mode': 'AUTO'}},
            'generationConfig': {'temperature': 0.4, 'maxOutputTokens': 4096},
        }
        return self.transport('request', {'model': model, 'body': body})

    @staticmethod
    def parse(response):
        candidates = response.get('candidates', [])
        if not candidates:
            raise ValueError('Gemini returned no candidate (possibly blocked)')
        candidate = candidates[0]
        if candidate.get('finishReason') != 'STOP':
            raise ValueError('Incomplete/blocked Gemini reply: ' + str(candidate.get('finishReason')))
        content = candidate.get('content')
        if not isinstance(content, dict) or not isinstance(content.get('parts'), list):
            raise ValueError('Malformed Gemini reply')
        calls, text = [], []
        for part in content['parts']:
            if part.get('text') and not part.get('thought'):
                text.append(part['text'])
            if 'functionCall' in part:
                call = part['functionCall']
                if not isinstance(call, dict) or not isinstance(call.get('name'), str) or not isinstance(call.get('args', {}), dict):
                    raise ValueError('Malformed Gemini tool call')
                calls.append(call)
        if len(calls) > 6:
            raise ValueError('At most six local tools per iteration')
        return content, calls, '\n'.join(text)[:12000]
