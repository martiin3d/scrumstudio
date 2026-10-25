import unittest
import test_storage
from store import run


class PlanningTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def item(self,key,title,**fields):
        return dict(key=key,title=title,description='Deliver '+title,acceptance_criteria=[title+' is independently verified'],scope_size='Small',scope_reason='One result with shared verification',**fields)

    def plan(self,p,items):
        return run('task_plan',dict(project=p,actor=self.agent,items=items))['items']

    def test_broad_refinement_exposes_independent_work_preserving_history_and_conflicts(self):
        p,_=self.project()
        broad=run('task_create',dict(project=p,title='Improve checkout',description='Address validation and receipt download',acceptance_criteria=['Both outcomes verified']))['id']
        run('task_activity',dict(project=p,id=broad,actor=self.human,kind='comment',text='Original planning context'))
        rows=self.plan(p,[self.item('address','Validate delivery address',id=broad,files=['address.py']),self.item('receipt','Download purchase receipt',files=['receipt.py']),self.item('release','Release checkout changes',dependencies=['address','receipt']),self.item('conflict','Update address labels',files=['address.py'],priority=1)])
        self.assertEqual(rows[0]['id'],broad)
        self.assertEqual(run('task_get',dict(project=p,id=rows[2]['id']))['dependencies'],[broad,rows[1]['id']])
        self.assertTrue(any(a['text']=='Original planning context' for a in run('task_activity',dict(project=p,id=broad))['items']))
        for row in rows:
            task=run('task_get',dict(project=p,id=row['id']))
            self.assertEqual(task['status'],'TODO');self.assertIsNone(task['owner_id']);self.assertFalse(task['locked'])
        self.assertTrue(run('task_claim',dict(project=p,id=broad,actor=self.agent))['claimed'])
        other=run('user_register',dict(identity='agent:planning-peer',name='Peer',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=other,client='codex',session_used_percent=10,weekly_used_percent=20))
        self.assertEqual(run('next_work',dict(project=p,actor=other))['task']['id'],rows[1]['id'])
        self.assertFalse(run('task_claim',dict(project=p,id=rows[3]['id'],actor=other))['claimed'])

    def test_cohesive_multifile_outcome_remains_one_unclaimed_task(self):
        p,_=self.project()
        rows=self.plan(p,[self.item('retry','Prevent duplicate receipt on retry',files=['api.py','storage.py','tests/test_receipt.py'])])
        self.assertEqual(len(rows),1)
        self.assertEqual(len(run('task_search',dict(project=p))['items']),1)
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])

    def test_invalid_plan_rolls_back_refinement_creation_and_activity(self):
        p,_=self.project()
        original=run('task_create',dict(project=p,title='Original',description='Keep history',acceptance_criteria=['Kept']))['id']
        before=run('task_activity',dict(project=p,id=original))
        with self.assertRaises(ValueError):
            self.plan(p,[self.item('first','Changed',id=original),self.item('second','Invalid dependency',dependencies=['missing'])])
        self.assertEqual(run('task_get',dict(project=p,id=original))['title'],'Original')
        self.assertEqual(run('task_activity',dict(project=p,id=original)),before)
        self.assertEqual(len(run('task_search',dict(project=p))['items']),1)
        with self.assertRaises(ValueError):
            self.plan(p,[self.item('a','One',dependencies=['b']),self.item('b','Two',dependencies=['a'])])
        self.assertEqual(len(run('task_search',dict(project=p))['items']),1)

    def test_active_claim_is_not_refined_and_quota_still_applies(self):
        p,_=self.project()
        row=self.plan(p,[self.item('one','One result')])[0]
        claim=run('task_claim',dict(project=p,id=row['id'],actor=self.agent))
        with self.assertRaises(ValueError):self.plan(p,[self.item('one','Changed result',id=row['id'])])
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',session_used_percent=93,weekly_used_percent=20))
        with self.assertRaises(ValueError):self.plan(p,[self.item('other','Extra result')])

    def test_final_dependency_graph_allows_valid_rewiring_in_one_batch(self):
        p,_=self.project()
        rows=self.plan(p,[self.item('a','First'),self.item('b','Second',dependencies=['a'])])
        changed=self.plan(p,[self.item('a','First revised',id=rows[0]['id'],dependencies=['b']),self.item('b','Second revised',id=rows[1]['id'],dependencies=[])])
        self.assertEqual(len(changed),2)
        self.assertEqual(run('task_get',dict(project=p,id=rows[0]['id']))['dependencies'],[rows[1]['id']])

    def handoff(self,p,task,token,items,**fields):
        from coordination_api import execute
        return execute('task_plan',dict(project=p,actor=self.agent,defer_task=task,token=token,prerequisites=['prereq'],items=items,**fields))

    def deferred_fixture(self):
        p,_=self.project()
        run('project_settings',dict(project=p,actor=self.human,agent_followup_tasks=True))
        task=self.plan(p,[self.item('current','Verify original candidate',files=['review.md'])])[0]['id']
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        return p,task,claim['token']

    def test_owned_handoff_links_prerequisite_preserves_scope_and_unblocks_natively(self):
        p,task,token=self.deferred_fixture()
        before=run('task_get',dict(project=p,id=task))
        result=self.handoff(p,task,token,[self.item('prereq','Implement missing topology',files=['engine.py'])],continuity='Done: verified original ledger; Next: missing topology support.')
        self.assertTrue(result['deferred']);prereq=result['items'][0]['id']
        parked=run('task_get',dict(project=p,id=task))
        self.assertEqual(parked['status'],'Blocked');self.assertEqual(parked['dependencies'],[prereq]);self.assertFalse(parked['locked'])
        for key in ('title','description','acceptance_criteria','files','evidence'):self.assertEqual(parked[key],before[key])
        self.assertFalse(parked['continuity_stale'])
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])
        selected=run('next_work',dict(project=p,actor=self.agent));self.assertEqual(selected['task']['id'],prereq)
        run('task_update',dict(project=p,id=prereq,actor=self.agent,status='Done',evidence='Targeted topology cases pass.'))
        reopened=run('task_get',dict(project=p,id=task));self.assertEqual(reopened['status'],'TODO');self.assertEqual(reopened['blocked_reason'],'')

    def test_handoff_cycle_rolls_back_everything_and_retains_claim(self):
        from planning import PlanRejected
        p,task,token=self.deferred_fixture()
        before=run('task_get',dict(project=p,id=task));locks=run('file_locks',dict(project=p))
        with self.assertRaisesRegex(PlanRejected,'cycle'):
            self.handoff(p,task,token,[self.item('prereq','Invalid circular prerequisite',dependencies=[task])])
        after=run('task_get',dict(project=p,id=task))
        self.assertEqual(after,before);self.assertEqual(run('file_locks',dict(project=p)),locks)
        self.assertEqual(len(run('task_search',dict(project=p))['items']),1)

    def test_handoff_rejects_foreign_expired_or_wrong_token_without_new_tasks(self):
        from unittest.mock import patch
        p,task,token=self.deferred_fixture()
        with self.assertRaisesRegex(ValueError,'claiming session'):self.handoff(p,task,'wrong-token',[self.item('prereq','New prerequisite')])
        other=run('user_register',dict(identity='agent:handoff-peer',name='Peer',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=other,client='codex',session_used_percent=10,weekly_used_percent=20))
        with self.assertRaisesRegex(ValueError,'your current'):
            run('task_plan',dict(project=p,actor=other,defer_task=task,token=token,prerequisites=['prereq'],items=[self.item('prereq','New prerequisite')]))
        with patch('planning.time.time',return_value=10**12):
            with self.assertRaises(ValueError):self.handoff(p,task,token,[self.item('prereq','New prerequisite')])
        self.assertEqual(len(run('task_search',dict(project=p))['items']),1)
        self.assertTrue(run('task_get',dict(project=p,id=task))['locked'])

    def test_handoff_requires_enabled_followups_and_respects_stop_and_quota(self):
        p,task,token=self.deferred_fixture();items=[self.item('prereq','New prerequisite')]
        run('project_settings',dict(project=p,actor=self.human,agent_followup_tasks=False))
        with self.assertRaisesRegex(ValueError,'human-enabled'):self.handoff(p,task,token,items)
        run('project_settings',dict(project=p,actor=self.human,agent_followup_tasks=True))
        run('project_control',dict(project=p,actor=self.human,action='stop'))
        with self.assertRaises(ValueError):self.handoff(p,task,token,items)
        run('project_control',dict(project=p,actor=self.human,action='wakeup',start_agents=False))
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',session_used_percent=93,weekly_used_percent=20))
        with self.assertRaises(ValueError):self.handoff(p,task,token,items)
        self.assertEqual(len(run('task_search',dict(project=p))['items']),1)

    def test_handoff_reuses_existing_prerequisite_without_duplicate(self):
        p,task,token=self.deferred_fixture()
        existing=self.plan(p,[self.item('existing','Known topology prerequisite')])[0]['id']
        result=self.handoff(p,task,token,[self.item('prereq','Known topology prerequisite')])
        self.assertEqual(result['items'][0]['id'],existing);self.assertTrue(result['items'][0]['existing'])
        self.assertEqual(len(run('task_search',dict(project=p))['items']),2)

    def test_handoff_releases_reserved_batch(self):
        p,_=self.project()
        run('project_settings',dict(project=p,actor=self.human,agent_followup_tasks=True))
        first,second=self.plan(p,[self.item('current','Current validation',files=['first.md'],context_group='shared'),self.item('next','Related validation',files=['second.md'],context_group='shared')])
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[first['id'],second['id']])
        claim=run('task_claim',dict(project=p,id=first['id'],actor=self.agent))
        self.handoff(p,first['id'],claim['token'],[self.item('prereq','Needed result')])
        self.assertIsNone(run('task_get',dict(project=p,id=second['id'])).get('batch'))
        other=run('user_register',dict(identity='agent:batch-peer',name='Peer',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=other,client='codex',session_used_percent=10,weekly_used_percent=20))
        self.assertTrue(run('task_claim',dict(project=p,id=second['id'],actor=other))['claimed'])
