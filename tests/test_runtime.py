import json
import time
import unittest
from unittest.mock import patch
import test_storage
from store import run
import runtime

class RuntimeTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def checkpoint(self,p,**fields):
        return run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',**fields))

    def test_automatic_resume_requires_opt_in_verified_reset_and_is_consumed_once(self):
        p,_=self.project()
        self.assertIs(run('project_settings',{'project':p})['auto_resume_session_quota'],False)
        with self.assertRaises(ValueError):
            run('project_settings',{'project':p,'actor':self.agent,'auto_resume_session_quota':True})
        run('project_settings',{'project':p,'actor':self.human,'auto_resume_session_quota':True})
        at=time.time();reset=at+10
        self.checkpoint(p,session_used_percent=93,weekly_used_percent=20,session_resets_at=reset,weekly_resets_at=at+10000,observed_at=at)
        with patch('runtime.time.time',return_value=at+11):
            fresh=dict(session_used_percent=2,weekly_used_percent=20,session_resets_at=at+18000,weekly_resets_at=at+10000,observed_at=at+11)
            # A weekly block holds the saved session-reset intent.
            self.assertFalse(self.checkpoint(p,action='observe',running=False,**dict(fresh,weekly_used_percent=94))['auto_resume']['requested'])
            self.assertTrue(self.checkpoint(p,action='observe',running=False,**fresh)['auto_resume']['requested'])
            self.assertFalse(self.checkpoint(p,action='observe',running=False,**fresh)['auto_resume']['requested'])
        self.assertEqual(run('agent_status',{'project':p})['agents'][0]['auto_resume_status'],'session reset verified; automatic resume requested')

    def test_auto_resume_cannot_override_manual_stop_pause_or_disable(self):
        for cancel in ('pause','stop','disable'):
            p,_=self.project(cancel)
            run('project_settings',{'project':p,'actor':self.human,'auto_resume_session_quota':True})
            at=time.time();reset=at+10
            self.checkpoint(p,session_used_percent=93,weekly_used_percent=20,session_resets_at=reset,weekly_resets_at=at+10000,observed_at=at)
            if cancel=='pause':
                self.checkpoint(p,action='pause',reason='Human-requested pause')
                self.checkpoint(p,action='observe',running=True,session_used_percent=93,weekly_used_percent=20,session_resets_at=reset,weekly_resets_at=at+10000,observed_at=at)
            elif cancel=='stop':
                run('project_control',{'project':p,'actor':self.human,'action':'stop'})
                run('project_control',{'project':p,'actor':self.human,'action':'wakeup','start_agents':False})
            else:run('project_settings',{'project':p,'actor':self.human,'auto_resume_session_quota':False})
            with patch('runtime.time.time',return_value=at+11):
                result=self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2,session_resets_at=at+18000,weekly_resets_at=at+10000,observed_at=at+11)
                self.assertFalse(result['auto_resume']['requested'],cancel)

    def test_profile_deletion_cancels_pending_reset_even_after_restore(self):
        p,_=self.project()
        run('project_settings',{'project':p,'actor':self.human,'auto_resume_session_quota':True})
        at=time.time()
        self.checkpoint(p,session_used_percent=93,weekly_used_percent=20,session_resets_at=at+10,weekly_resets_at=at+10000,observed_at=at)
        run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        run('user_manage',{'actor':self.human,'id':self.agent,'action':'restore'})
        with patch('runtime.time.time',return_value=at+11):
            result=self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2,session_resets_at=at+18000,weekly_resets_at=at+10000,observed_at=at+11)
            self.assertFalse(result['auto_resume']['requested'])

    def test_auto_resume_rejects_stale_ambiguous_and_same_window_telemetry(self):
        p,_=self.project()
        run('project_settings',{'project':p,'actor':self.human,'auto_resume_session_quota':True,'usage_guard':False})
        at=time.time();reset=at+10
        self.checkpoint(p,session_used_percent=93,weekly_used_percent=20,session_resets_at=reset,weekly_resets_at=at+10000,observed_at=at)
        for delta,observed,next_reset,weekly_reset in [(200,at,at+18000,at+10000),(11,at+11,reset,at+10000),(11,at+11,at+18000,None)]:
            with patch('runtime.time.time',return_value=at+delta):
                result=self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2,session_resets_at=next_reset,weekly_resets_at=weekly_reset,observed_at=observed)
                self.assertFalse(result['auto_resume']['requested'])

    def test_quota_handoff_and_no_duplicate_notes(self):
        p,path=self.project()
        task=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Small outcome','files':['one.py']})['id']
        run('task_claim',{'project':p,'id':task,'actor':self.agent})
        state=run('agent_status',{'project':p})
        self.assertEqual((state['active_agents'],state['active_tasks']),(1,1))
        result=self.checkpoint(p,session_used_percent=92,weekly_used_percent=40,summary='API contract implemented; tests pending.')
        self.assertTrue(result['should_stop'])
        self.assertEqual(result['tasks_released'],1)
        self.assertEqual(run('task_get',{'project':p,'id':task})['status'],'TODO')
        self.assertEqual(run('file_locks',{'project':p})['items'],[])
        notes=run('snapshot',{'project':p})['notes']
        self.assertEqual(notes,[])
        self.assertEqual(run('project_chat',{'project':p})['items'][0]['actor'],self.agent)
        self.checkpoint(p)
        self.assertEqual(len(run('snapshot',{'project':p})['notes']),0)
        with self.assertRaises(ValueError):
            run('task_claim',{'project':p,'id':task,'actor':self.agent})
        self.assertTrue(run('next_work',{'project':p,'actor':self.agent})['should_stop'])
        self.checkpoint(p,session_used_percent=10,weekly_used_percent=94)
        self.assertTrue(run('next_work',{'project':p,'actor':self.agent})['should_stop'])

    def test_stop_wakeup_permissions_unknown_and_reset(self):
        p,_=self.project()
        task=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Test'})['id']
        with self.assertRaises(ValueError):
            run('project_control',{'project':p,'actor':self.agent,'action':'stop'})
        stopped=run('project_control',{'project':p,'actor':self.human,'action':'stop'})
        with self.assertRaises(ValueError):
            run('task_claim',{'project':p,'actor':self.agent,'id':task})
        self.assertTrue(self.checkpoint(p)['should_stop'])
        woke=run('project_control',{'project':p,'actor':self.human,'action':'wakeup','start_agents':False})
        self.assertGreater(woke['control']['generation'],stopped['control']['generation'])
        self.assertFalse(self.checkpoint(p)['should_stop'])
        self.assertTrue(self.checkpoint(p,session_used_percent=1)['should_stop'])
        self.assertTrue(self.checkpoint(p,session_used_percent=1,weekly_used_percent=2,session_resets_at=time.time()-1)['should_stop'])
        self.checkpoint(p,session_used_percent=1,weekly_used_percent=2)
        with patch('runtime.time.time',return_value=time.time()+200):
            self.assertTrue(self.checkpoint(p)['should_stop'])
            self.assertEqual(run('agent_status',{'project':p})['active_tasks'],0)

    def test_runtime_not_exported_or_restored(self):
        p,path=self.project()
        run('project_control',{'project':p,'actor':self.human,'action':'stop'})
        run('project_export',{'project':p})
        data=(path/'.scrum/snapshot.json').read_text()
        for field in ['agent_sessions','heartbeat','usage_at','project_control']:
            self.assertNotIn(field,data)
        run('project_restore',{'project':p,'confirm':True})
        self.assertFalse(run('agent_status',{'project':p})['control']['paused'])

    def test_bridge_observation_never_releases_running_worker(self):
        p,_=self.project()
        task=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Editing','files':['edit.py']})['id']
        run('task_claim',{'project':p,'actor':self.agent,'id':task})
        result=self.checkpoint(p,action='observe',running=True,session_used_percent=92,weekly_used_percent=40)
        self.assertTrue(result['should_stop'])
        self.assertEqual(result['tasks_released'],0)
        self.assertTrue(run('task_get',{'project':p,'id':task})['locked'])
        self.assertTrue(run('agent_status',{'project':p})['agents'][0]['bridge_connected'])
        self.assertEqual(self.checkpoint(p,action='pause',reason='Worker process exited')['tasks_released'],1)

    def test_wakeup_without_bridge_reports_no_worker_even_with_todo_tasks(self):
        p,_=self.project()
        task=run('task_create',dict(project=p,title='Available work',description='An independent outcome',acceptance_criteria=['Outcome verified']))['id']
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=run('project_control',dict(project=p,actor=self.human,action='wakeup',start_agents=False))
            spawn.assert_not_called()
        self.assertTrue(result['signal_recorded'])
        readiness=result['wakeup']
        self.assertEqual(readiness['status'],'no_connected_bridge')
        self.assertEqual(readiness['connected_bridges'],0)
        self.assertFalse(readiness['can_receive_signal'])
        self.assertFalse(readiness['workers_started'])
        self.assertIn('default Codex',readiness['next_step'])
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'TODO')
        self.assertEqual(run('agent_status',dict(project=p))['wakeup']['status'],'no_connected_bridge')

    def test_wakeup_distinguishes_ready_running_and_quota_held_bridges(self):
        p,_=self.project()
        self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2)
        result=run('project_control',dict(project=p,actor=self.human,action='wakeup',start_agents=False))['wakeup']
        self.assertEqual((result['status'],result['ready_bridges'],result['connected_bridges']),('ready',1,1))
        self.assertFalse(result['workers_started'])
        self.checkpoint(p,action='observe',running=True)
        result=run('agent_status',dict(project=p))['wakeup']
        self.assertEqual((result['status'],result['running_bridges']),('already_running',1))
        for fields,reason in [(dict(session_used_percent=93,weekly_used_percent=2),'5h usage'),(dict(session_used_percent=1,weekly_used_percent=95),'weekly usage'),(dict(session_used_percent=1,weekly_used_percent=2,observed_at=0),'unknown or stale'),(dict(session_used_percent=1,weekly_used_percent=2,session_resets_at=time.time()-1),'unknown or reset')]:
            with self.subTest(reason=reason):
                self.checkpoint(p,action='observe',running=False,**fields)
                result=run('project_control',dict(project=p,actor=self.human,action='wakeup',start_agents=False))['wakeup']
                self.assertEqual((result['status'],result['held_bridges'],result['ready_bridges']),('held',1,0))
                self.assertIn(reason,result['held'][0]['reason'])

    def test_wakeup_status_expires_with_bridge_and_does_not_bypass_stop(self):
        p,_=self.project()
        self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2)
        run('project_control',dict(project=p,actor=self.human,action='stop'))
        self.assertEqual(run('agent_status',dict(project=p))['wakeup']['status'],'stopped')
        self.assertTrue(self.checkpoint(p)['should_stop'])
        result=run('project_control',dict(project=p,actor=self.human,action='wakeup',start_agents=False))
        self.assertEqual(result['wakeup']['status'],'ready')
        self.assertFalse(result['control']['paused'])
        with patch('runtime.time.time',return_value=time.time()+91):
            result=run('agent_status',dict(project=p))['wakeup']
            self.assertEqual(result['status'],'no_connected_bridge')

    def test_wakeup_reports_mixed_recipients_without_hiding_usage_holds(self):
        p,_=self.project()
        other=run('user_register',dict(identity='agent:second',name='Second',kind='agent'))['id']
        self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2)
        run('agent_checkpoint',dict(project=p,actor=other,client='codex',action='observe',running=False,session_used_percent=93,weekly_used_percent=2))
        result=run('project_control',dict(project=p,actor=self.human,action='wakeup',start_agents=False))['wakeup']
        self.assertEqual((result['ready_bridges'],result['held_bridges'],result['connected_bridges']),(1,1,2))
        self.assertEqual(result['held'][0]['name'],'Second')

    def test_failed_cli_is_not_reported_as_ready_and_recovers_on_running(self):
        p,_=self.project()
        self.checkpoint(p,action='observe',running=False,session_used_percent=1,weekly_used_percent=2)
        self.checkpoint(p,action='offline',run_exit_code=1,run_error='Configured model is unsupported')
        self.checkpoint(p,action='observe',running=False)
        readiness=run('agent_status',dict(project=p))['wakeup']
        self.assertEqual(readiness['status'],'failed')
        self.assertEqual(readiness['failed_bridges'],1)
        self.assertIn('unsupported',readiness['next_step'])
        self.checkpoint(p,action='observe',running=True)
        self.assertEqual(run('agent_status',dict(project=p))['wakeup']['failed_bridges'],0)


class TaskHandoffTest(RuntimeTest):
    def test_repeated_sessions_do_not_recursively_copy_handoff(self):
        p,_=self.project()
        task=run('task_create',dict(project=p,title='Resume',description='Preserve progress',acceptance_criteria=['Verified']))['id']
        for _ in range(3):
            self.checkpoint(p,session_used_percent=1,weekly_used_percent=2)
            run('task_claim',dict(project=p,id=task,actor=self.agent))
            self.checkpoint(p,action='pause',summary='API checked; next verify integration.')
            t=run('task_get',dict(project=p,id=task))
            self.assertEqual(t['continuity'].count('API checked'),1)
            self.assertEqual(t['continuity'].count('Paused:'),1)
            self.assertNotIn('notes',t)
        notes=run('task_get',dict(project=p,id=task,include_notes=True,notes_content=True))['notes']['items']
        self.assertEqual(len(notes),0)
        self.assertTrue(all('API checked' not in str(note) for note in notes))

    def test_pause_preserves_recorded_progress_and_remaining_work(self):
        p,folder=self.project()
        task=run('task_create',dict(project=p,title='Handoff',description='Bounded work',acceptance_criteria=['Behavior verified'],checklist=[{'text':'Implement','done':True},{'text':'Verify','done':False}],files=['file.py']))['id']
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],log='Implementation drafted; checks pending.'))
        result=self.checkpoint(p,action='pause',summary='Stopped at checkpoint',reason='Human Stop')
        self.assertEqual(result['tasks_released'],1)
        t=run('task_get',dict(project=p,id=task))
        self.assertEqual(t['status'],'TODO')
        self.assertFalse(t['locked']);self.assertFalse(t['continuity_stale'])
        self.assertIn('Implementation drafted; checks pending.',t['continuity'])
        self.assertEqual(t['checklist'],[{'text':'Implement','done':True},{'text':'Verify','done':False}])
        self.assertEqual(t['files'],['file.py'])
        self.assertNotIn('Prior continuity',t['continuity'])
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])
