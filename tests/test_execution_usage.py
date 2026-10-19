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
