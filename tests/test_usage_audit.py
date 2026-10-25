import json
from pathlib import Path
import unittest
from unittest.mock import patch,Mock
import tempfile
import test_storage
from store import run
import execution_usage
import usage_audit

class UsageAuditTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def test_old_in_process_flow_module_keeps_unknown_fields_null(self):
        p,root=self.project()
        with patch('telemetry.metrics',return_value={'completed_tasks':2}):
            result=run('coordination_metrics',dict(project=p,include_audit=True,start_at=0,end_at=1))['audit']
        self.assertIsNone(result['flow']['attempted_tasks'])
        self.assertEqual(result['flow']['completed_tasks'],2)
        self.assertEqual(result['live_executions'],[])

    def test_opt_in_isolated_and_shared_run_not_divided(self):
        p,root=self.project();task=run('task_create',dict(project=p,title='Recover',description='One verified recovery',acceptance_criteria=['Published'],category='Git recovery'))['id']
        report=dict(run_id='one',model='fixture',tasks=[task],source='codex-terminal-turn',input_tokens=100,output_tokens=10,cached_input_tokens=80,cache_write_input_tokens=0,reasoning_output_tokens=2)
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='offline',cli_usage_report=report))
        self.assertNotIn('audit',run('coordination_metrics',dict(project=p)))
        result=run('coordination_metrics',dict(project=p,include_audit=True))['audit']
        self.assertEqual(result['recorded']['runs'],1)
        self.assertEqual(result['recorded']['known_totals']['uncached_input_tokens'],20)
        self.assertEqual(result['top_runs'][0]['tasks'][0]['id'],task)
        self.assertTrue(result['recommendations'])
        other,_=self.project('Other');self.assertEqual(run('coordination_metrics',dict(project=other,include_audit=True))['audit']['recorded']['runs'],0)
        empty=run('coordination_metrics',dict(project=p,include_audit=True,start_at=0,end_at=1))['audit']
        self.assertEqual(empty['recorded']['runs'],0)

    def test_logs_are_bounded_deduplicated_and_never_export_text(self):
        p,root=self.project();path=root/'.scrum/local/worker-codex-fixture.log'
        events=[{'type':'thread.started','thread_id':'one'}, {'type':'item.completed','item':{'text':'SECRET not for audit'}}, {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':2,'cached_input_tokens':90}}, {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':2,'cached_input_tokens':90}}, {'type':'thread.started','thread_id':'unknown'}]
        path.write_text('\n'.join(json.dumps(x) for x in events))
        result=run('coordination_metrics',dict(project=p,include_audit=True,include_logs=True))['audit']
        h=result['historical_logs'];self.assertEqual(h['runs'],2);self.assertEqual(h['known_runs'],1)
        self.assertEqual(h['known_totals']['input_tokens'],100)
        self.assertNotIn('SECRET',json.dumps(result));self.assertTrue(h['partial'])

    def test_unknown_cache_and_verification_repetitions_are_honest(self):
        totals=usage_audit.totals([{'input_tokens':100,'output_tokens':2},{}])
        self.assertIsNone(totals['known_totals']['cached_input_tokens']);self.assertTrue(totals['partial'])
        p,root=self.project();path=root/'.scrum/local/verification-events.jsonl'
        path.write_text('\n'.join(json.dumps(r) for r in [{'at':1,'signature':'same','passed':False,'kind':'missing_report'}, {'at':2,'signature':'same','passed':False,'kind':'missing_report'}, {'at':3,'passed':True,'reused':True}]))
        result=usage_audit.verification_events(root,0,4)
        self.assertEqual((result['executed'],result['reused'],result['repeat_failures']),(2,1,1))
        self.assertEqual(usage_audit.verification_events(root,2.5,4)['events'],1)

class NativePublicationTest(unittest.TestCase):
    def test_only_transport_failures_retry_same_refspec(self):
        import git_sync
        failure=Mock(returncode=128,stderr='Could not resolve host: example.invalid')
        success=Mock(returncode=0,stderr='')
        with patch.object(git_sync,'_git',side_effect=[failure,success]) as git:
            result,attempts=git_sync.publish_verified('root','origin','fixedsha:refs/heads/main')
        self.assertEqual(attempts,2);self.assertEqual(result.returncode,0)
        self.assertEqual(git.call_args_list[0],git.call_args_list[1])
        with patch.object(git_sync,'_git',return_value=Mock(returncode=1,stderr='non-fast-forward permission denied')) as git:
            _,attempts=git_sync.publish_verified('root','origin','fixedsha:refs/heads/main')
        self.assertEqual(attempts,1);git.assert_called_once()
