import unittest
import test_storage
from store import run

class BatchTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def test_same_small_context_group_can_share_a_run_without_file_overlap(self):
        p,_=self.project()
        first=self.task(p,files=['first.py'],context_group='one-component')
        second=self.task(p,files=['second.py'],context_group='one-component',dependencies=[first])
        third=self.task(p,files=['unrelated.py'])
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[first,second])
        claim=run('task_claim',dict(project=p,id=first,actor=self.agent))
        with self.assertRaisesRegex(ValueError,'reserved'):
            run('task_claim',dict(project=p,id=second,actor=self.human))
        result=run('work_finish',dict(project=p,id=first,actor=self.agent,token=claim['token'],client='codex',use_batch=True,claim_next=True,evidence='First outcome verified'))
        self.assertTrue(result['next']['claim']['claimed'])
        self.assertTrue(result['next']['ownership_granted'])
        self.assertEqual(result['next']['task']['id'],second)
        self.assertEqual(result['next']['task']['acceptance_criteria'],['Verified'])
        self.assertEqual(run('task_get',dict(project=p,id=first))['status'],'Done')
        self.assertEqual(run('task_get',dict(project=p,id=second))['status'],'In Progress')
        self.assertEqual(run('task_get',dict(project=p,id=third))['status'],'TODO')

    def test_explicit_context_reserves_six_bounded_outcomes_and_reuses_one_run(self):
        p,_=self.project();tasks=[]
        for i in range(7):
            tasks.append(self.task(p,files=[f'component-{i}/part-{n}.cs' for n in range(12)],context_group='shared-runtime',scope_size='Unknown',scope_reason='Bounded shared component',dependencies=tasks[-1:]))
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],tasks[:6])
        claim=run('task_claim',dict(project=p,id=tasks[0],actor=self.agent))
        for i,task in enumerate(tasks[:6]):
            self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'In Progress')
            if i<5:
                self.assertEqual(run('task_get',dict(project=p,id=tasks[i+1]))['status'],'TODO')
                with self.assertRaisesRegex(ValueError,'reserved'):
                    run('task_claim',dict(project=p,id=tasks[i+1],actor=self.human))
            result=run('work_finish',dict(project=p,id=task,actor=self.agent,token=claim['token'],client='codex',use_batch=True,claim_next=True,evidence='Independent outcome verified'))
            if i<5:
                self.assertEqual(result['next']['task']['id'],tasks[i+1])
                self.assertTrue(result['next']['claim']['claimed'])
                self.assertEqual(len(result['next']['task']['files']),12)
                claim=result['next']['claim']
            else:self.assertTrue(result['next']['batch_done'])
        self.assertEqual(run('task_get',dict(project=p,id=tasks[6]))['status'],'TODO')

    def test_context_batch_budgets_do_not_split_file_plans_or_include_large_work(self):
        p,_=self.project()
        root=self.task(p,files=[f'root-{n}.py' for n in range(40)],context_group='cohesive',scope_size='Unknown')
        second=self.task(p,files=[f'next-{n}.py' for n in range(40)],context_group='cohesive',scope_size='Unknown')
        large=self.task(p,files=['large.py'],context_group='cohesive',scope_size='Large')
        excessive=self.task(p,files=[f'wide-{n}.py' for n in range(41)],context_group='cohesive',scope_size='Unknown')
        self.task(p,files=[f'third-{n}.py' for n in range(40)],context_group='cohesive',scope_size='Unknown')
        self.task(p,files=['over-budget.py'],context_group='cohesive',scope_size='Unknown')
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([x['id'] for x in offer['batch']][:2],[root,second])
        self.assertEqual(len(offer['batch']),3)
        self.assertNotIn(large,[x['id'] for x in offer['batch']])
        self.assertNotIn(excessive,[x['id'] for x in offer['batch']])
        self.assertEqual(len(run('task_get',dict(project=p,id=excessive,full=True))['files']),41)

    def test_explicit_context_can_reserve_unplanned_files_but_not_claim_them(self):
        p,_=self.project();first=self.task(p,context_group='shared')
        second=self.task(p,context_group='shared')
        run('task_update',dict(project=p,id=second,files=[]))
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[first,second])
        self.assertFalse(run('task_get',dict(project=p,id=second))['locked'])
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])

    def test_dependency_only_block_can_follow_prerequisite_in_same_context(self):
        p,_=self.project();first=self.task(p,context_group='engine')
        second=self.task(p,context_group='engine',dependencies=[first],priority=1)
        external=self.task(p,context_group='engine',dependencies=[first])
        run('task_update',dict(project=p,id=second,actor=self.human,status='Blocked',blocked_reason='Wait for prerequisite',blocked_reason_kind='dependency'))
        run('task_update',dict(project=p,id=external,actor=self.human,status='Blocked',blocked_reason='Physical sample required',blocked_reason_kind='external'))
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[first,second])
        claim=run('task_claim',dict(project=p,id=first,actor=self.agent))
        result=run('work_finish',dict(project=p,id=first,actor=self.agent,token=claim['token'],client='codex',use_batch=True,claim_next=True,evidence='Prerequisite passed'))
        self.assertEqual(result['next']['task']['id'],second)
        self.assertTrue(result['next']['claim']['claimed'])
        self.assertEqual(run('task_get',dict(project=p,id=second))['blocked_reason'],'')
        self.assertEqual(run('task_get',dict(project=p,id=external))['status'],'Blocked')

    def test_task_delivery_batch_integrates_each_branch_before_claiming_next(self):
        import test_low_turns
        p,folder=self.project();git=test_low_turns.GitAutomationTest.repo(self,folder)
        run('project_settings',dict(project=p,actor=self.human,git_mode='Branches',git_base_branch='main',git_merge='Task',git_push='Never',usage_guard=False))
        first=self.task(p,files=['a.py'],context_group='shared-delivery')
        second=self.task(p,files=['b.py'],context_group='shared-delivery',dependencies=[first])
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([x['id'] for x in offer['batch']],[first,second])
        claim=run('task_claim',dict(project=p,id=first,actor=self.agent))
        (folder/'a.py').write_text('first verified outcome\n')
        result=run('work_finish',dict(project=p,id=first,actor=self.agent,token=claim['token'],client='codex',use_batch=True,claim_next=True,evidence='First behavior verified'))
        self.assertEqual(result['completed']['status'],'Done')
        self.assertEqual(git('show','main:a.py'),'first verified outcome')
        self.assertTrue(result['next']['claim']['claimed'])
        self.assertEqual(git('branch','--show-current'),'scrum/'+result['next']['task']['code'].lower())
        self.assertEqual((folder/'a.py').read_text(),'first verified outcome\n')
        self.assertEqual(git('status','--porcelain'),'')
        (folder/'b.py').write_text('second verified outcome\n')
        finished=run('work_finish',dict(project=p,id=second,actor=self.agent,token=result['next']['claim']['token'],client='codex',use_batch=True,claim_next=True,evidence='Second behavior verified'))
        self.assertTrue(finished['next']['batch_done'])
        self.assertEqual(git('show','main:b.py'),'second verified outcome')

    def test_recovery_is_not_mixed_with_implementation_batch(self):
        p,_=self.project()
        recovery=self.task(p,category='Git recovery',context_group='shared')
        implementation=self.task(p,context_group='shared')
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[recovery])
        run('worker_launch',dict(project=p,actor=self.agent,action='release'))

    def test_large_own_reservation_page_does_not_block_verified_close(self):
        p,_=self.project();paths=[f'generated/{i}.txt' for i in range(205)]
        task=self.task(p,files=paths)
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        own=run('session_resume',dict(project=p,actor=self.agent,closing_task=task))
        self.assertTrue(own['own_file_locks']['has_more'])
        self.assertFalse(own['other_own_file_locks'])
        result=run('work_finish',dict(project=p,id=task,actor=self.agent,token=claim['token'],client='codex',close_session=True,evidence='Generated-file migration verified'))
        self.assertEqual(result['completed']['status'],'Done')
        self.assertFalse(run('session_resume',dict(project=p,actor=self.agent))['own_file_locks']['items'])

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
        fields=dict(title='Related outcome',description='One independently checked change',acceptance_criteria=['Verified'],files=files or ['shared.py'],scope_size='Small',scope_reason='Bounded result');fields.update(extra)
        return run('task_create',dict(project=p,**fields))['id']

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

