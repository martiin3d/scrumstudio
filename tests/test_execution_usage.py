import json
from pathlib import Path
import tempfile
import unittest
import test_storage
from store import run
import execution_usage

class UsageTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def test_interaction_windows_keep_duplicate_resets_and_missing_values_distinct(self):
        path=self.root/'samples.log'
        sample=lambda total,last=None:{'type':'usage.sample','total':total,'last':last}
        counts=lambda tokens,cache:{'input_tokens':tokens,'cached_input_tokens':cache}
        tool={'kind':'command','tags':['read'],'failed':True,'retry':False,'output_bytes':42}
        events=[{'type':'thread.started'},sample(counts(100,80)),{'type':'tool.observed','tool':tool},
                sample(counts(100,80)),sample(counts(140,90)),sample(counts(5,2)),sample({}),
                sample(None,counts(30,20)),{'type':'tool.observed','tool':tool},
                {'type':'turn.completed','usage':counts(200,150)}]
        path.write_text(''.join(json.dumps(row)+'\n' for row in events),encoding='utf-8')
        report=execution_usage.interaction_report(path)
        self.assertEqual([window['uncached_input_tokens'] for window in report['windows']],[20,30,None,None,10])
        self.assertEqual([window['source'] for window in report['windows']],
                         ['cumulative_delta','cumulative_delta','unavailable','unavailable','provider_last'])
        self.assertEqual(report['windows'][1]['tools_since_last_sample'],[tool])
        self.assertEqual(report['tools_without_following_sample'],[tool])
        self.assertEqual(report['terminal_uncached_input_tokens'],50)
        path.write_text(json.dumps({'type':'result','usage':{'input_tokens':4,'cache_read_input_tokens':10}}))
        claude=execution_usage.interaction_report(path,client='claude')
        self.assertEqual(claude['terminal_uncached_input_tokens'],4)
        self.assertEqual(claude['windows'],[])

    def test_tool_observation_flags_exact_failed_command_retry_and_excludes_content(self):
        seen=set()
        command='Get-Content private.txt'
        failed=execution_usage.tool_observation({'type':'commandExecution','command':command,
            'aggregatedOutput':'private é','exitCode':1},seen)
        retry=execution_usage.tool_observation({'type':'commandExecution','command':command,'exitCode':0},seen)
        self.assertEqual(failed,{'kind':'command','tags':['read'],'failed':True,'retry':False,'output_bytes':10})
        self.assertTrue(retry['retry'])
        self.assertIsNone(retry['output_bytes'])
        self.assertEqual(seen,set())

    def test_six_task_batch_telemetry_matches_planner_and_mcp_schema(self):
        from mcp_server import handle
        from context_batches import CONTEXT_BATCH_SIZE
        p,_=self.project();tasks=[run('task_create',dict(project=p,title='Batch outcome '+str(i),description='Telemetry fixture outcome',acceptance_criteria=['Valid telemetry is retained']))['id'] for i in range(CONTEXT_BATCH_SIZE)]
        report=execution_usage.report(self.root/'missing',0,'codex','six-task-batch','',tasks)
        response=handle(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='agent_checkpoint',arguments=dict(project=p,actor=self.agent,client='codex',action='offline',cli_usage_report=report))))
        self.assertFalse(response['result'].get('isError',False),response)
        result=run('coordination_metrics',dict(project=p,include_cli_usage=True))['cli_usage']
        self.assertEqual(result['runs'],1)

    def test_supervisor_telemetry_does_not_expand_worker_schema(self):
        from unittest.mock import patch
        import sys
        from mcp_server import handle
        request=dict(jsonrpc='2.0',id=1,method='tools/list')
        normal=handle(request)['result']['tools']
        with patch.object(sys,'argv',['mcp_server.py','--worker-schema']):worker=handle(request)['result']['tools']
        full=next(t for t in normal if t['name']=='agent_checkpoint')['inputSchema']
        small=next(t for t in worker if t['name']=='agent_checkpoint')['inputSchema']
        self.assertIn('cli_usage_report',full['properties'])
        self.assertNotIn('cli_usage_report',small['properties'])
        self.assertIn('session_used_percent',small['properties'])
        self.assertLess(len(json.dumps(small)),len(json.dumps(full)))

    def test_supported_events_ignore_tool_text_and_preserve_unknowns(self):
        path=self.root/'run.log'
        path.write_text(json.dumps({'type':'item.completed','item':{'text':'tokens=99999'}})+'\n'+json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':80}})+'\n')
        counters=execution_usage.parse(path,0,'codex')
        self.assertEqual(counters['input_tokens'],100)
        self.assertEqual(counters['cached_input_tokens'],80)
        self.assertIsNone(counters['reasoning_output_tokens'])
        self.assertIsNone(execution_usage.parse(path,path.stat().st_size,'codex'))
        path.write_text(json.dumps({'type':'result','usage':{'input_tokens':4,'output_tokens':2,'cache_read_input_tokens':8,'cache_creation_input_tokens':3}})+'\n')
        self.assertEqual(execution_usage.parse(path,0,'claude')['cache_write_input_tokens'],3)
        path.write_text(json.dumps({'type':'turn.completed','usage':{'input_tokens':True}})+'\n')
        self.assertIsNone(execution_usage.parse(path,0,'codex'))

    def test_checkpoint_deduplicates_shared_run_and_summary_is_opt_in(self):
        project,_=self.project()
        tasks=[run('task_create',dict(project=project,title='Shared',description='Shared context',acceptance_criteria=['Verified']))['id'] for _ in range(2)]
        path=self.root/'run.log';path.write_text(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':80,'cache_write_input_tokens':0,'reasoning_output_tokens':5}})+'\n')
        report=execution_usage.report(path,0,'codex','run-one','fixture',tasks)
        args=dict(project=project,actor=self.agent,client='codex',action='offline',cli_usage_report=report)
        run('agent_checkpoint',args);run('agent_checkpoint',args)
        self.assertNotIn('cli_usage',run('coordination_metrics',dict(project=project)))
        summary=run('coordination_metrics',dict(project=project,include_cli_usage=True))['cli_usage']
        self.assertEqual((summary['runs'],summary['input_tokens'],summary['output_tokens'],summary['cached_input_tokens']),(1,100,20,80))
        bad=run('agent_checkpoint',args|{'cli_usage_report':report|{'input_tokens':200}})
        self.assertIn('cli_usage_error',bad)
        self.assertEqual(bad['status'],'offline')
        other,_=self.project('Other')
        self.assertEqual(run('coordination_metrics',dict(project=other,include_cli_usage=True))['cli_usage']['runs'],0)

    def test_unavailable_usage_is_not_zero_and_bad_report_cannot_block_pause(self):
        p,_=self.project()
        report=execution_usage.report(self.root/'missing',0,'codex','unknown','',[])
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='offline',cli_usage_report=report))
        summary=run('coordination_metrics',dict(project=p,include_cli_usage=True))['cli_usage']
        self.assertEqual(summary['known_runs'],0);self.assertIsNone(summary['input_tokens'])
        result=run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='pause',cli_usage_report={'invalid':True}))
        self.assertTrue(result['should_stop']);self.assertIn('cli_usage_error',result)
