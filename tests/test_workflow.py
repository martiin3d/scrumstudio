import unittest
import test_app
import test_storage
import json
from store import run


class CompactWorkflowTest(unittest.TestCase):
    setUp = test_app.WorkflowTest.setUp
    tearDown = test_app.WorkflowTest.tearDown

    def test_start_and_finish_preserve_checks(self):
        start = run('work_start', {'project': self.p, 'actor': self.actor, 'client': 'codex', 'session_used_percent': 5, 'weekly_used_percent': 6})
        self.assertEqual(start['actor'], self.actor)
        self.assertFalse(start['checkpoint']['should_stop'])
        self.assertIn('own_file_locks', start)
        task = run('task_create', {'project': self.p, 'title': 'Verify', 'description': 'Verify compact completion.', 'acceptance_criteria': ['Checks remain enforced.']})
        claim = run('task_claim', {'project': self.p, 'actor': self.actor, 'id': task['id']})
        args = dict(project=self.p, actor=self.actor, id=task['id'], token=claim['token'], evidence='')
        with self.assertRaises(ValueError):
            run('work_finish', args)
        self.assertEqual(run('task_get', {'project': self.p, 'id': task['id']})['status'], 'In Progress')
        result = run('work_finish', args | {'evidence': 'Scenario verified.'})
        self.assertEqual(result['completed']['status'], 'Done')
        self.assertNotIn('instruction', result['next'])

    def test_changed_settings_omitted_and_quota_stops(self):
        policy = run('project_settings', {'project': self.p})
        result = run('work_start', {'project': self.p, 'actor': self.actor, 'client': 'codex', 'settings_seen': policy['settings_version'], 'session_used_percent': 99, 'weekly_used_percent': 6})
        self.assertNotIn('settings', result)
        self.assertTrue(result['checkpoint']['should_stop'])
        self.assertNotIn('next', result)

    def task(self):
        return run('task_create', {'project':self.p,'title':'Small fix','description':'One bounded fix.','acceptance_criteria':['Verified.']})['id']

    def start(self, task, **extra):
        return run('work_start',dict(project=self.p,actor=self.actor,client='codex',id=task,claim=True,session_used_percent=2,weekly_used_percent=3,**extra))

    def test_two_call_light_lifecycle(self):
        task=self.task()
        result=self.start(task)
        self.assertEqual(result['workflow']['effective'],'Light')
        self.assertTrue(result['claim']['claimed'])
        closed=run('work_finish',dict(project=self.p,actor=self.actor,id=task,token=result['claim']['token'],evidence='Regression passes.',close_session=True,client='codex',retrospective_items=['No additional process improvement observed in this bounded fix.']))
        self.assertEqual(closed['completed']['status'],'Done')
        self.assertEqual(closed['session']['status'],'offline')
        self.assertNotIn('next',closed)

    def test_human_retrospective_does_not_block_light_claim(self):
        task=self.task()
        retro=run('note_add',dict(project=self.p,kind='Retrospective',title='Process',items=['Check regressions.']))
        result=self.start(task)
        self.assertTrue(result['claim']['claimed'])
        self.assertNotIn('retrospective',result)

    def test_standard_policy_and_dependencies_fall_back(self):
        task=self.task()
        human=run('user_register',dict(identity='human:policy',name='Policy human',kind='human'))['id']
        run('project_settings',dict(project=self.p,actor=human,workflow_mode='Standard'))
        self.assertEqual(self.start(task)['workflow']['effective'],'Standard')
        run('project_settings',dict(project=self.p,actor=human,workflow_mode='Light'))
        dep=self.task()
        run('task_update',dict(project=self.p,id=task,dependencies=[dep]))
        self.assertNotIn('claim',self.start(task))
        with self.assertRaises(ValueError):
            run('project_settings',dict(project=self.p,actor=human,workflow_mode='Invalid'))

    def test_concurrent_workers_claim_in_one_start_call(self):
        task=self.task()
        other=run('user_register',dict(identity='agent:second',name='Second',kind='agent'))['id']
        run('agent_checkpoint',dict(project=self.p,actor=other,client='codex',session_used_percent=1,weekly_used_percent=1))
        result=self.start(task)
        self.assertEqual(result['workflow']['effective'],'Standard')
        self.assertTrue(result['claim']['claimed'])
        self.assertEqual(result['task']['status'],'In Progress')
        rival=run('work_start',dict(project=self.p,actor=other,client='codex',id=task,claim=True,session_used_percent=1,weekly_used_percent=1))
        self.assertFalse(rival.get('claim',{}).get('claimed'))
        self.assertEqual(run('task_get',dict(project=self.p,id=task))['owner_id'],self.actor)

    def test_concurrent_workers_and_bad_close_do_not_complete(self):
        task=self.task()
        other=run('user_register',dict(identity='agent:second',name='Second',kind='agent'))['id']
        run('agent_checkpoint',dict(project=self.p,actor=other,client='codex',session_used_percent=1,weekly_used_percent=1))
        started=self.start(task)
        self.assertEqual(started['workflow']['effective'],'Standard')
        claim=started['claim']
        with self.assertRaises(ValueError):
            run('work_finish',dict(project=self.p,actor=self.actor,id=task,token=claim['token'],evidence='Checked',close_session=True,client='codex',retrospective_items=['']))
        self.assertEqual(run('task_get',dict(project=self.p,id=task))['status'],'In Progress')

    def test_close_needs_no_process_prose_and_keeps_optional_notes_scoped(self):
        for extra,count in [({},0),({'retrospective_items':[]},0),({'retrospective_items':['Observed improvement'],'review_items':['Human decision needed']},2)]:
            task=self.task()
            claim=run('task_claim',dict(project=self.p,actor=self.actor,id=task))
            closed=run('work_finish',dict(project=self.p,actor=self.actor,id=task,token=claim['token'],evidence='Regression verified',close_session=True,client='codex',**extra))
            self.assertEqual(closed['session']['status'],'offline')
            notes=run('task_get',dict(project=self.p,id=task,include_notes=True,notes_audience='all'))['notes']['items']
            self.assertEqual(len(notes),count)
            run('agent_checkpoint',dict(project=self.p,actor=self.actor,client='codex',session_used_percent=1,weekly_used_percent=2))


class WorkflowStorageTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def test_mode_roundtrip_and_old_snapshot_default(self):
        project, folder=self.project()
        run('project_settings',dict(project=project,actor=self.human,workflow_mode='Standard'))
        run('project_export',dict(project=project))
        clone,_=self.clone(folder)
        self.assertEqual(run('project_settings',dict(project=clone['id']))['workflow_mode'],'Standard')
        path=folder/'.scrum/snapshot.json'
        snapshot=json.loads(path.read_text(encoding='utf-8'))
        snapshot['settings'].pop('workflow_mode')
        path.write_text(json.dumps(snapshot),encoding='utf-8')
        run('project_restore',dict(project=project,confirm=True))
        self.assertEqual(run('project_settings',dict(project=project))['workflow_mode'],'Auto')


class DecisionPayloadTest(unittest.TestCase):
    def test_diagnostics_removed_but_stops_and_conflicts_preserved(self):
        from workflow import decision_payload
        result=decision_payload({'active_tasks':0,'checkpoint':{'should_stop':False,'control':{'paused':0,'generation':8},'tasks_released':0,'check_after_seconds':300},'next':{'capacity':{'active':0,'limit':1},'file_conflicts_skipped':2,'blocked_total':0,'task':None},'own_file_locks':{'items':[],'has_more':False,'next_cursor':0}})
        self.assertNotIn('control',result['checkpoint'])
        self.assertNotIn('active_tasks',result)
        self.assertTrue(result['next']['no_candidate'])
        self.assertEqual(result['next']['file_conflicts_skipped'],2)
        self.assertFalse(result['own_file_locks']['has_more'])
        stopped=decision_payload({'checkpoint':{'should_stop':True,'reason':'Quota','tasks_released':1,'control':{'paused':1,'reason':'Human Stop'}}})
        self.assertTrue(stopped['checkpoint']['should_stop'])
        self.assertEqual(stopped['checkpoint']['tasks_released'],1)
        self.assertEqual(stopped['checkpoint']['control']['reason'],'Human Stop')
