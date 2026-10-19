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
