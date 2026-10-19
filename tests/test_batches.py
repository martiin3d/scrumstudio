import unittest
import test_storage
from store import run

class BatchTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def test_planned_context_group_is_portable_and_does_not_reserve_work(self):
        import web_refresh
        p,folder=self.project()
        first=self.task(p,context_group='session-storage')
        second=self.task(p)
        before=run('task_get',dict(project=p,id=second))
        item={k:before[k] for k in ('id','title','description','acceptance_criteria')}
        item.update(key='existing',context_group='session-storage')
        run('task_plan',dict(project=p,actor=self.agent,items=[item]))
        after=run('task_get',dict(project=p,id=second))
        self.assertEqual(after['context_group'],'session-storage')
        for key in ('status','continuity','evidence','dependencies','checklist','owner_id','locked'):
            self.assertEqual(after[key],before[key])
        data=web_refresh.refresh(dict(project=p,page='Board'),run)['data']
        self.assertEqual(data['taskGroups'],[])
        self.assertEqual({t['context_group'] for t in data['tasks']},{'session-storage'})
        run('project_export',dict(project=p))
        clone,_=self.clone(folder)
        self.assertEqual(run('task_get',dict(project=clone['id'],id=first))['context_group'],'session-storage')
        run('task_update',dict(project=p,id=second,actor=self.agent,context_group=''))
        self.assertNotIn('context_group',run('task_get',dict(project=p,id=second)))
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])

    def test_invalid_group_rolls_back_and_foreign_claim_is_respected(self):
        p,_=self.project();task=self.task(p)
        before=run('task_get',dict(project=p,id=task))
        with self.assertRaises(ValueError):
            run('task_update',dict(project=p,id=task,actor=self.agent,context_group='x'*81,title='Wrong'))
        self.assertEqual(run('task_get',dict(project=p,id=task))['title'],before['title'])
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        with self.assertRaises(ValueError):
            run('task_update',dict(project=p,id=task,actor=self.human,context_group='foreign'))
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],context_group='owned'))
        self.assertEqual(run('task_get',dict(project=p,id=task))['context_group'],'owned')

    def test_human_batch_history_survives_release_but_not_restore(self):
        import json
        from pathlib import Path
        import web_refresh
        p,folder=self.project();tasks=[self.task(p) for _ in range(2)]
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        data=web_refresh.refresh(dict(project=p,page='Board'),run)['data']
        group=data['taskGroups'][0]
        self.assertEqual(group['tasks'],tasks);self.assertTrue(group['active'])
        self.assertEqual(group['reserved_tasks'],tasks)
        self.assertNotIn('taskGroups',run('snapshot',dict(project=p)))
        self.assertNotIn('context_batch',run('task_get',dict(project=p,id=tasks[0])))
        run('worker_launch',dict(project=p,actor=self.agent,action='release'))
        groups=web_refresh.refresh(dict(project=p,page='Board'),run)['data']['taskGroups']
        self.assertEqual(groups[0]['id'],group['id']);self.assertFalse(groups[0]['active'])
        self.assertEqual(set(groups[0]['tasks']),set(tasks))
        run('project_export',dict(project=p))
        exported=json.loads((folder/'.scrum/snapshot.json').read_text(encoding='utf-8'))
        self.assertNotIn('context_batch_history',exported['tables'])
        run('project_restore',dict(project=p,confirm=True))
        self.assertEqual(web_refresh.refresh(dict(project=p,page='Board'),run)['data']['taskGroups'],[])

    def test_identical_batch_offer_reuses_group_and_latest_membership_wins(self):
        import context_batches
        p,_=self.project();tasks=[self.task(p) for _ in range(2)]
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        first=context_batches.browser_groups(p,tasks)[0]['id']
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual(context_batches.browser_groups(p,tasks)[0]['id'],first)
        run('worker_launch',dict(project=p,actor=self.agent,action='release'))
        tasks.append(self.task(p))
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        newest=context_batches.browser_groups(p,tasks)
        self.assertEqual(len(newest),1);self.assertNotEqual(newest[0]['id'],first)
        self.assertEqual(set(newest[0]['tasks']),set(tasks))

    def task(self,p,files=None,**extra):
        return run('task_create',dict(project=p,title='Related outcome',description='One independently checked change',acceptance_criteria=['Verified'],files=files or ['shared.py'],scope_size='Small',scope_reason='Bounded result',**extra))['id']

    def finish(self,p,task):
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        return run('work_finish',dict(project=p,id=task,actor=self.agent,token=claim['token'],client='codex',use_batch=True,evidence='Fixture check passed'))

    def test_three_related_tasks_share_context_but_only_current_is_claimed(self):
        p,_=self.project();tasks=[self.task(p) for _ in range(4)]
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],tasks[:3])
        self.assertNotIn('description',offer['batch'][0])
        claim=run('task_claim',dict(project=p,id=tasks[0],actor=self.agent))
        self.assertEqual(run('task_get',dict(project=p,id=tasks[1]))['status'],'TODO')
        result=run('work_finish',dict(project=p,id=tasks[0],actor=self.agent,token=claim['token'],client='codex',use_batch=True,evidence='First verified'))
        self.assertEqual(result['next']['task']['id'],tasks[1])
        self.assertFalse(result['next']['ownership_granted'])
        for key in ('description','acceptance_criteria','evidence','continuity','checklist','attachments'):
            self.assertNotIn(key,result['next']['task'])
        self.assertEqual(run('task_get',dict(project=p,id=tasks[1]))['status'],'TODO')
        repeated=run('work_finish',dict(project=p,id=tasks[0],actor=self.agent,token=claim['token'],client='codex',use_batch=True,evidence='First verified'))
        self.assertEqual(repeated['next']['task']['id'],tasks[1])
        self.assertEqual(self.finish(p,tasks[1])['next']['task']['id'],tasks[2])
        self.assertTrue(self.finish(p,tasks[2])['next']['batch_done'])
        self.assertEqual(run('task_get',dict(project=p,id=tasks[3]))['status'],'TODO')
        released=run('worker_launch',dict(project=p,actor=self.agent,action='release'))
        self.assertTrue(released['batch_complete'])

    def test_context_model_dependencies_and_foreign_claims_rechecked(self):
        p,_=self.project();first=self.task(p)
        dependent=self.task(p,dependencies=[first])
        other=self.task(p,files=['unrelated.py'])
        manual=self.task(p,model='fixed-other',actor=self.human)
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[first,dependent])
        # A future task is reserved, but a human can explicitly release the batch.
        run('task_update',dict(project=p,id=dependent,dependencies=[],files=['elsewhere.py']))
        with self.assertRaisesRegex(ValueError,'reserved'):
            run('task_claim',dict(project=p,id=dependent,actor=self.human))
        run('task_release',dict(project=p,id=dependent,actor=self.human,force=True))
        held=run('task_claim',dict(project=p,id=dependent,actor=self.human))
        result=self.finish(p,first)
        self.assertTrue(result['next']['batch_done'])
        self.assertEqual(run('task_get',dict(project=p,id=dependent))['owner_id'],self.human)
        self.assertEqual(run('task_get',dict(project=p,id=other))['status'],'TODO')
        self.assertEqual(run('task_get',dict(project=p,id=manual))['status'],'TODO')

    def test_manual_model_change_cuts_batch_and_partial_release_does_not_continue(self):
        p,_=self.project();first,second=self.task(p),self.task(p)
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        run('task_update',dict(project=p,id=second,actor=self.human,model='different'))
        self.assertTrue(self.finish(p,first)['next']['batch_done'])
        self.assertEqual(run('task_get',dict(project=p,id=second))['status'],'TODO')
        run('worker_launch',dict(project=p,actor=self.agent,action='release'))
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',session_used_percent=1,weekly_used_percent=2))
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertFalse(run('worker_launch',dict(project=p,actor=self.agent,action='release'))['batch_complete'])

    def test_batch_reservations_claim_paths_expiry_and_handoff(self):
        import time
        import storage
        p,_=self.project();first,second=self.task(p),self.task(p)
        run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        for actor in (self.human,self.agent):
            with self.assertRaisesRegex(ValueError,'reserved'):
                run('task_claim',dict(project=p,id=second,actor=actor))
        with self.assertRaisesRegex(ValueError,'reserved'):
            run('task_update',dict(project=p,id=second,actor=self.human,claim=True))
        self.assertIsNone(run('next_work',dict(project=p,actor=self.human))['task'])
        full=run('task_get',dict(project=p,id=second,full=True))
        self.assertEqual([t['id'] for t in full['context_batch']['tasks']],[first,second])
        self.assertNotIn('context_batch',run('task_get',dict(project=p,id=second)))
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='offline'))
        self.assertNotIn('context_batch',run('task_get',dict(project=p,id=second,full=True)))
        self.assertEqual(run('next_work',dict(project=p,actor=self.human))['task']['id'],first)

