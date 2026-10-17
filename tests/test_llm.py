import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from studio import agent, llm, strata_tasks, jobs as server, project as db
import test_studio as baseline


class ProviderTests(unittest.TestCase):
    setUp = baseline.StudioTests.setUp
    tearDown = baseline.StudioTests.tearDown

    def definition(self):
        return [agent.tool('submit_result', 'Return result', {'ok': {'type': 'boolean'}}, ['ok'])]

    def result(self, value=None):
        return {'stopReason': 'end_turn', 'text': json.dumps(value or {
            'calls': [{'name': 'submit_result', 'arguments': {'ok': True}}]}),
            'usage': {'input_tokens': 10, 'output_tokens': 5},
            'modelUsage': {'test-model': {}}}

    def test_provider_is_scoped_and_thread_local(self):
        self.assertEqual(llm.current(), 'strata')
        seen = []
        with llm.using('grok'):
            thread = threading.Thread(target=lambda: seen.append(llm.current()))
            thread.start(); thread.join()
            self.assertEqual(llm.current(), 'grok')
        self.assertEqual(seen, ['strata'])
        self.assertEqual(llm.current(), 'strata')
        with self.assertRaises(db.StudioError):
            with llm.using('https://untrusted.invalid'): pass

    def test_dispatch_and_structured_retry_preserve_failed_output(self):
        calls = []
        def transport(messages, tools, timeout):
            calls.append(messages.copy())
            arguments = '{' if len(calls) == 1 else json.dumps({'items': [{'id': 'a', 'text': '帽子'}]})
            return {'role': 'assistant', 'tool_calls': [{'id': str(len(calls)), 'function': {
                'name': 'submit_result', 'arguments': arguments}}]}, {}
        with llm.using('grok'), patch.object(llm, 'grok_chat', side_effect=transport):
            value = strata_tasks.translate('test', [{'id': 'a', 'text': 'hat'}], 'ja')
        self.assertEqual(value[0]['text'], '帽子')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][-2]['role'], 'assistant')
        self.assertEqual(calls[1][-1]['role'], 'tool')
        self.assertIn('error', json.loads(calls[1][-1]['content']))
        logs = list((db.directory('test')/'agent-runs').glob('task-*.json'))
        self.assertEqual(db.read_json(logs[0])['provider'], 'grok')

    def test_cli_boundary_limits_tools_and_uses_isolated_text_input(self):
        def run(command, **kwargs):
            self.assertNotIn('shell', kwargs)
            self.assertEqual(command[command.index('--max-turns')+1], '1')
            self.assertEqual(command[command.index('--reasoning-effort')+1], 'low')
            self.assertEqual(command[command.index('--tools')+1], 'read_file')
            self.assertIn('read_file', command[command.index('--disallowed-tools')+1])
            self.assertIn('--no-subagents', command)
            self.assertIn('MCPTool', command)
            prompt = Path(command[command.index('--prompt-file')+1])
            self.assertEqual(prompt.suffix, '.txt')
            self.assertEqual(prompt.parent, kwargs['cwd'])
            self.assertEqual(json.loads(prompt.read_text(encoding='utf-8'))['conversation'][0]['content'], 'テスト')
            self.assertNotIn('NOVELAI_API_KEY', kwargs['env'])
            self.assertNotIn('UNRELATED_TOKEN', kwargs['env'])
            return subprocess.CompletedProcess(command, 0, json.dumps(self.result()), '')
        with patch.object(db, 'ROOT', self.root), patch.object(llm, 'grok_executable', return_value='grok.exe'), \
             patch.dict(os.environ, {'NOVELAI_API_KEY': 'never-forward', 'UNRELATED_TOKEN': 'never-forward'}), \
             patch.object(subprocess, 'run', side_effect=run) as send:
            message, usage = llm.grok_chat([{'role': 'user', 'content': 'テスト'}], self.definition(), 10)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']), {'ok': True})
        self.assertNotIn('total_cost_usd', usage)
        self.assertEqual(list((self.root/'.runtime'/'grok-requests').iterdir()), [])

    def test_timeout_and_process_error_never_retry_or_expose_diagnostics(self):
        for error in (subprocess.TimeoutExpired('grok', 1),
                      subprocess.CompletedProcess('grok', 1, 'secret-output', 'secret-error')):
            with patch.object(db, 'ROOT', self.root), patch.object(llm, 'grok_executable', return_value='grok.exe'), \
                 patch.object(subprocess, 'run') as run:
                if isinstance(error, Exception): run.side_effect = error
                else: run.return_value = error
                with self.assertRaises(db.StudioError) as caught:
                    llm.grok_chat([], self.definition(), 1)
                self.assertNotIn('secret', str(caught.exception))
                self.assertEqual(run.call_count, 1)

    def test_unknown_tool_and_incomplete_result_are_rejected(self):
        value = self.result({'calls': [{'name': 'run_terminal_cmd', 'arguments': {}}]})
        with self.assertRaises(db.StudioError): llm.parse_grok_result(value, self.definition())
        value = self.result(); value['stopReason'] = 'max_tokens'
        with self.assertRaises(db.StudioError): llm.parse_grok_result(value, self.definition())
        value = self.result(); value['structured_output'] = {'calls': [{'name': 'submit_result', 'arguments': {'ok': False}}]}
        value['total_cost_usd'] = .01
        message, usage = llm.parse_grok_result(value, self.definition())
        self.assertFalse(json.loads(message['tool_calls'][0]['function']['arguments'])['ok'])
        self.assertEqual(usage['total_cost_usd'], .01)

    def test_wrong_nested_types_are_repairable_studio_errors(self):
        for value in ({'pages': '[]'}, {'pages': [None]}, {'pages': [{'panels': '[]'}]},
                      {'characters': [None]}, {'characters': [{'outfits': '[]'}]},
                      {'pages': [{'panels': [{'characters': [None]}]}]}):
            with self.subTest(value=value), self.assertRaises(db.StudioError): agent.bilingual_board(value)

    def test_malformed_results_stop_after_two_attempts_without_saving(self):
        before = db.load('test')
        malformed = [None, [], [{'id': 'a', 'function': None}],
                     [{'id': 'a', 'function': {'name': 'submit_result', 'arguments': '{'}}],
                     [{'id': 'a', 'function': {'name': 'submit_result', 'arguments': '{"items":[null]}'}}]]
        for calls in malformed:
            with self.subTest(calls=calls), llm.using('grok'), \
                 patch.object(llm, 'grok_chat', return_value=({'role':'assistant','tool_calls':calls}, {})) as send:
                with self.assertRaisesRegex(db.StudioError, '2回'):
                    strata_tasks.translate('test', [{'id':'a','text':'hat'}], 'ja')
                self.assertEqual(send.call_count, 2)
                self.assertEqual(db.load('test'), before)

    def test_provider_reaches_page_tasks_production_and_agent_process(self):
        p = db.load('test')
        cases = [('review', strata_tasks, 'review', {'warnings': []}),
                 ('voices', strata_tasks, 'voices', None),
                 ('rewrite', strata_tasks, 'rewrite_page', p),
                 ('wardrobe', strata_tasks, 'wardrobe', None),
                 ('produce', server.production, 'produce', {'stage':'warnings'})]
        for kind, module, method, result in cases:
            observed = []
            def task(*args, **kwargs):
                observed.append(llm.current())
                return result
            job = {'id':'selected-'+kind, 'kind':kind}
            data = {'project_id':'test','revision':p['revision'],'llm_provider':'grok',
                    'page_number':1,'character_id':p['characters'][0]['id']}
            with self.subTest(kind=kind), patch.object(module,method,side_effect=task), \
                 patch.object(server,'compose'), patch.object(server,'save_job'):
                server.job_run(job,data)
            self.assertEqual(job['status'], 'complete', job.get('message'))
            self.assertEqual(observed, ['grok'])
        with patch.object(server.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run, \
             patch.object(server,'save_job'):
            job = {'id':'selected-agent','kind':'agent'}
            server.job_run(job,{'project_id':'test','llm_provider':'grok'})
        self.assertEqual(job['status'],'complete')
        command=run.call_args.args[0]
        self.assertEqual(command[command.index('--provider')+1],'grok')

    def test_job_runs_with_selected_provider_and_invalid_choice_is_rejected(self):
        job = {'id': 'provider-test', 'kind': 'translate'}
        observed = []
        def translate(*args):
            observed.append(llm.current())
            return []
        with patch.object(strata_tasks, 'translate', side_effect=translate), patch.object(server, 'save_job'):
            server.job_run(job, {'project_id': 'test', 'llm_provider': 'grok', 'items': [], 'target': 'ja'})
        self.assertEqual(observed, ['grok'])
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(llm.current(), 'strata')
        with self.assertRaises(db.StudioError):
            server.start_job({'request_id': 'bad-provider', 'kind': 'translate',
                              'project_id': 'test', 'llm_provider': '../../other'})


if __name__ == '__main__':
    unittest.main()
