import json
import os
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
