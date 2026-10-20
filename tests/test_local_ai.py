"""Local inference settings and real HTTP round trips, without an AI service."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from studio import agent, cli, llm, local_ai, project as db


class LocalAITests(unittest.TestCase):
    def setUp(self):
        self.runtime = db.ROOT / '.runtime'
        self.root = self.runtime / ('local-ai-test-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(self.cleanup)
        self.patches = [patch.object(db, 'ROOT', self.root), patch.dict(os.environ)]
        for item in self.patches:
            item.start()
        for name in ('LOCAL_AI_URL', 'LOCAL_AI_MODEL'):
            os.environ.pop(name, None)
        local_ai.settings.cache_clear()

    def cleanup(self):
        local_ai.settings.cache_clear()
        for item in reversed(self.patches):
            item.stop()
        target = self.root.resolve()
        assert target.is_relative_to(self.runtime.resolve()) and target.name.startswith('local-ai-test-')
        shutil.rmtree(target)

    def env(self, body):
        (self.root / '.env').write_text(body, encoding='utf-8-sig')
        local_ai.settings.cache_clear()

    def test_defaults_missing_blank_and_environment_precedence(self):
        self.assertEqual(local_ai.settings().url, local_ai.DEFAULT_URL)
        self.assertEqual(local_ai.settings().model, local_ai.DEFAULT_MODEL)
        self.env('LOCAL_AI_URL=\nLOCAL_AI_MODEL=   \n')
        self.assertEqual(local_ai.settings().url, local_ai.DEFAULT_URL)
        self.assertEqual(local_ai.settings().model, local_ai.DEFAULT_MODEL)
        self.env('LOCAL_AI_URL= # default\nLOCAL_AI_MODEL=   # default\n')
        self.assertEqual(local_ai.settings().url, local_ai.DEFAULT_URL)
        self.assertEqual(local_ai.settings().model, local_ai.DEFAULT_MODEL)
        self.env('export LOCAL_AI_URL="https://inference.invalid:9443/proxy/v1/chat/completions" # host\n'
                 "LOCAL_AI_MODEL='local/model-v2' # alias\nUNRELATED_SECRET=unrelated-marker\n")
        config = local_ai.settings()
        self.assertEqual(config.models_url, 'https://inference.invalid:9443/proxy/v1/models')
        self.assertEqual(config.model, 'local/model-v2')
        self.assertNotIn('LOCAL_AI_URL', os.environ)
        self.assertNotIn('unrelated-marker', repr(config))
        os.environ.update(LOCAL_AI_URL='http://[::1]:9999/v1/chat/completions', LOCAL_AI_MODEL='override')
        local_ai.settings.cache_clear()
        self.assertEqual(local_ai.settings().models_url, 'http://[::1]:9999/v1/models')
        self.assertEqual(local_ai.settings().model, 'override')

    def test_root_dotenv_is_used_from_work_directory_and_cached_until_restart(self):
        self.env('LOCAL_AI_MODEL=first\n')
        work = self.root / 'work' / 'example'
        work.mkdir(parents=True)
        (work / '.env').write_text('LOCAL_AI_MODEL=wrong', encoding='utf-8')
        previous = Path.cwd()
        try:
            os.chdir(work)
            self.assertEqual(local_ai.settings().model, 'first')
        finally:
            os.chdir(previous)
        (self.root / '.env').write_text('LOCAL_AI_MODEL=second', encoding='utf-8')
        self.assertEqual(local_ai.settings().model, 'first')
        local_ai.settings.cache_clear()
        self.assertEqual(local_ai.settings().model, 'second')

    def test_invalid_configuration_is_rejected_without_exposing_values(self):
        invalid = [
            'LOCAL_AI_URL=http://sensitive-host:70000/v1/chat/completions',
            'LOCAL_AI_URL=http://sensitive-host/v1',
            'LOCAL_AI_URL=http://user:sensitive-marker@host/v1/chat/completions',
            'LOCAL_AI_URL=http://host/v1/chat/completions?token=sensitive-marker',
            'LOCAL_AI_URL=http://host/v1/chat/completions#sensitive-marker',
            'LOCAL_AI_URL=file:///sensitive-marker/chat/completions',
            'LOCAL_AI_URL=http://host\\sensitive-marker/v1/chat/completions',
            'LOCAL_AI_MODEL=sensitive marker',
            'LOCAL_AI_MODEL="sensitive-marker',
            'LOCAL_AI_MODEL=one\nLOCAL_AI_MODEL=sensitive-marker',
            'LOCAL_AI_URL=http://host/v1/chat/completions\nLOCAL_AI_URL=',
        ]
        for body in invalid:
            with self.subTest(setting=body.split('=')[0]):
                self.env(body)
                with patch.object(local_ai, '_open') as send:
                    with self.assertRaises(db.StudioError) as caught:
                        agent.chat([], [], 1)
                    send.assert_not_called()
                self.assertNotIn('sensitive', str(caught.exception))
                self.assertFalse(local_ai.check()['connected'])

    def server(self):
        self.requests = []
        self.mode = 'ok'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length)) if length else None
                owner.requests.append((self.command, self.path, body))
                if owner.mode == 'redirect':
                    self.send_response(307)
                    self.send_header('Location', '/redirected')
                    self.end_headers()
                    return
                status = 503 if owner.mode == 'error' else 200
                if self.command == 'GET':
                    data = {'data': [{'id': 'other' if owner.mode == 'missing' else 'configured-model'}]}
                else:
                    data = {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{
                        'id': 'call-1', 'type': 'function',
                        'function': {'name': 'submit_result', 'arguments': '{"ok":true}'}}]}}],
                            'usage': {'total_tokens': 9}}
                raw = b'sensitive-server-error' if owner.mode in ('error', 'malformed') else json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = handle_request
            do_POST = handle_request

        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        def stop():
            http.shutdown()
            http.server_close()
            thread.join()
        self.addCleanup(stop)
        self.env(f'LOCAL_AI_URL=http://localhost:{http.server_port}/proxy/v1/chat/completions\n'
                 'LOCAL_AI_MODEL=configured-model\nUNRELATED_SECRET=never-forward\n')

    def test_custom_host_port_path_model_and_tools_round_trip(self):
        self.server()
        messages = [{'role': 'user', 'content': 'テスト'}]
        definitions = [agent.tool('submit_result', 'Return result')]
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
            self.assertTrue(local_ai.check()['connected'])
            message, usage = agent.chat(messages, definitions, 5)
        self.assertEqual(self.requests[0], ('GET', '/proxy/v1/models', None))
        method, path, body = self.requests[1]
        self.assertEqual((method, path), ('POST', '/proxy/v1/chat/completions'))
        self.assertEqual(body['model'], 'configured-model')
        self.assertEqual(body['messages'], messages)
        self.assertEqual(body['tools'], definitions)
        self.assertEqual((body['tool_choice'], body['max_tokens'], body['reasoning_effort']), ('auto', 8192, 'none'))
        self.assertNotIn('never-forward', json.dumps(body))
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), {'ok': True})
        self.assertEqual(usage, {'total_tokens': 9})

    def test_probe_is_read_only_and_failures_never_retry_or_leak(self):
        self.server()
        for mode in ('missing', 'error', 'malformed', 'redirect'):
            self.mode = mode
            before = len(self.requests)
            report = local_ai.check()
            self.assertFalse(report['connected'])
            self.assertNotIn('sensitive', json.dumps(report))
            self.assertNotIn('configured-model', json.dumps(report))
            self.assertEqual(self.requests[before:], [('GET', '/proxy/v1/models', None)])
        for mode in ('error', 'malformed', 'redirect'):
            self.mode = mode
            before = len(self.requests)
            with self.assertRaises(db.StudioError) as caught:
                agent.chat([], [], 5)
            self.assertNotIn('sensitive', str(caught.exception))
            self.assertEqual(len(self.requests) - before, 1)
        with patch.object(local_ai, '_open', side_effect=TimeoutError('sensitive-timeout')) as send:
            with self.assertRaises(db.StudioError) as caught:
                agent.chat([], [], 3)
            self.assertNotIn('sensitive', str(caught.exception))
            self.assertEqual(send.call_count, 1)

    def test_grok_bypasses_invalid_local_settings(self):
        self.env('LOCAL_AI_URL=invalid\n')
        with llm.using('grok'), patch.object(llm, 'grok_chat', return_value=({}, {})) as grok:
            self.assertEqual(agent.chat([], [], 7), ({}, {}))
            grok.assert_called_once_with([], [], 7)

    def test_cli_probe_reports_status_without_settings(self):
        self.server()
        output = io.StringIO()
        with patch('sys.argv', ['studio.cli', 'check-local-ai']), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        self.assertTrue(json.loads(output.getvalue())['connected'])
        self.assertNotIn('configured-model', output.getvalue())
        self.assertEqual(self.requests, [('GET', '/proxy/v1/models', None)])


if __name__ == '__main__':
    unittest.main()
