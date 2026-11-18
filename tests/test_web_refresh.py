import json
import os
import time
import unittest
from unittest.mock import Mock,patch
import test_storage
from store import run
import web_refresh
import idle_signals

class IdleRefreshTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def test_unchanged_page_skips_snapshot_and_detects_task_edits(self):
        p,_=self.project()
        with patch('web_refresh.time.time',return_value=180):
            first=web_refresh.refresh({'project':p},run)
            self.assertTrue(first['version'])
            spy=Mock(wraps=run)
            small=web_refresh.refresh({'project':p,'version_seen':first['version']},spy)
            self.assertFalse(small['changed']);spy.assert_not_called()
            self.assertLess(len(json.dumps(small)),80)
            run('task_create',dict(project=p,title='New task',description='A bounded outcome',acceptance_criteria=['Checked']))
            changed=web_refresh.refresh({'project':p,'version_seen':first['version']},run)
            self.assertTrue(changed['changed']);self.assertEqual(len(changed['data']['tasks']),1)

    def test_agents_page_excludes_task_and_note_content_and_stale_views_expire(self):
        p,_=self.project()
        with patch('web_refresh.time.time',return_value=180):
            spy=Mock(wraps=run);first=web_refresh.refresh({'project':p,'page':'Agents'},spy)
            self.assertNotIn('tasks',first['data']);self.assertNotIn('notes',first['data'])
            self.assertNotIn('snapshot',[c.args[0] for c in spy.call_args_list])
        with patch('web_refresh.time.time',return_value=241):
            self.assertTrue(web_refresh.refresh({'project':p,'page':'Agents','version_seen':first['version']},run)['changed'])

    def test_metrics_session_filter_is_forwarded_and_invalidates_view(self):
        p,_=self.project()
        with patch('web_refresh.time.time',return_value=1700000180):
            first=web_refresh.refresh({'project':p,'page':'Metrics'},run)
            spy=Mock(wraps=run)
            selected=web_refresh.refresh({'project':p,'page':'Metrics','filter_session':'selected','version_seen':first['version']},spy)
            self.assertTrue(selected['changed'])
            spy.assert_any_call('coordination_metrics',{'project':p,'include_periods':True,'filter_session':'selected','start_at':selected['data']['metrics']['start_at'],'end_at':selected['data']['metrics']['end_at']})
            self.assertEqual(selected['data']['coordination']['totals']['calls'],0)

    def test_work_period_bounds_reach_both_metrics_calls(self):
        import metrics_periods
        p,_=self.project();now=1700000180
        with patch('web_refresh.time.time',return_value=now):
            metrics_periods.capture(p,'project_control',{}, {'control':{'generation':1,'changed':now-500,'paused':0}})
            spy=Mock(wraps=run)
            result=web_refresh.refresh({'project':p,'page':'Metrics','filter_period':'wakeup:1'},spy)
            self.assertEqual(result['data']['metrics']['start_at'],now-500)
            self.assertEqual(result['data']['metrics']['end_at'],now)
            self.assertTrue(result['data']['coordination']['interval_filtered'])
            spy.assert_any_call('project_metrics',{'project':p,'days':30,'include_series':True,'filter_period':'wakeup:1'})

    def test_metrics_show_scoped_tokens_by_provider_and_keep_unknown_counts(self):
        project,_=self.project()
        other,_=self.project('Other')
        now=time.time()

        def record(run_id,client,input_tokens,cached,output,write=0,at=now,scope=project):
            source={'claude':'claude-run-summary','codex':'codex-terminal-turn'}[client]
            report=dict(run_id=run_id,model='',tasks=[],source=source if input_tokens is not None else 'unavailable',
                        input_tokens=input_tokens,cached_input_tokens=cached,output_tokens=output,
                        cache_write_input_tokens=write,reasoning_output_tokens=None)
            with patch('execution_usage.time.time',return_value=at):
                run('agent_checkpoint',dict(project=scope,actor=self.agent,client=client,action='offline',cli_usage_report=report))

        record('codex-known','codex',100,80,10)
        record('claude-known','claude',30,40,5,write=10)
        record('codex-missing','codex',None,None,None,write=None)
        record('old','codex',500,300,20,at=now-10*86400)
        record('separate-workspace','codex',900,100,20,scope=other)
        spy=Mock(wraps=run)
        usage=web_refresh.refresh({'project':project,'page':'Metrics','days':7},spy)['data']['tokenUsage']
        self.assertEqual((usage['known_runs'],usage['runs'],usage['partial']),(2,3,True))
        self.assertEqual((usage['input_tokens'],usage['cached_input_tokens'],usage['uncached_input_tokens'],usage['output_tokens'],usage['total_tokens']),(180,120,60,15,195))
        self.assertEqual([(r['client'],r['known_runs'],r['runs'],r['total_tokens']) for r in usage['providers']],
                         [('claude',1,1,85),('codex',1,2,110)])
        self.assertNotIn('cli_usage_audit',[call.args[0] for call in spy.call_args_list])
        empty=run('cli_usage_overview',dict(project=project,start_at=0,end_at=1))
        self.assertEqual(empty['runs'],0)
        self.assertIsNone(empty['total_tokens'])

    def test_control_signal_is_only_updated_by_control_mutations(self):
        p,folder=self.project();before=idle_signals.stamp(folder)
        run('project_settings',{'project':p})
        self.assertEqual(idle_signals.stamp(folder),before)
        run('project_control',dict(project=p,actor=self.human,action='stop'))
        self.assertNotEqual(idle_signals.stamp(folder),before)
        stamp=idle_signals.stamp(folder)
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='observe',running=False))
        self.assertEqual(idle_signals.stamp(folder),stamp)

    def test_legacy_single_file_operations_never_signal_registered_checkouts(self):
        with patch.dict(os.environ,{'SCRUM_DB':'fixture.db'},clear=True),patch('storage.entry') as entry:
            idle_signals.notify('project_control',{'project':1,'action':'stop'})
            entry.assert_not_called()

    def test_checkpoint_schedule_keeps_signals_and_execution_transitions_prompt(self):
        self.assertFalse(idle_signals.checkpoint_due(59,0,False,False,False))
        self.assertTrue(idle_signals.checkpoint_due(60,0,False,False,False))
        self.assertFalse(idle_signals.checkpoint_due(9,0,True,True,False))
        self.assertTrue(idle_signals.checkpoint_due(10,0,True,True,False))
        self.assertTrue(idle_signals.checkpoint_due(2,0,True,True,True))
        self.assertTrue(idle_signals.checkpoint_due(2,0,False,True,False))
        self.assertTrue(idle_signals.checkpoint_due(2,0,True,False,False))

    def test_clean_bridge_exit_disconnects_immediately(self):
        p,_=self.project()
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='observe',running=False))
        self.assertTrue(run('agent_status',dict(project=p))['agents'][0]['bridge_connected'])
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='offline'))
        self.assertFalse(run('agent_status',dict(project=p))['agents'][0]['bridge_connected'])
