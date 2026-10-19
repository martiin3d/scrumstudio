import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_storage
from store import run


class LaunchTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def worker(self,p,name):
        actor=run('user_register',dict(identity='agent:'+name,name=name,kind='agent'))['id']
        self.quota(p,actor)
        return actor

    def quota(self,p,actor):
        run('agent_checkpoint',dict(project=p,actor=actor,client='codex',session_used_percent=10,weekly_used_percent=20))

    def task(self,p,name,**extra):
        return run('task_create',dict(project=p,title=name,description='Independently verifiable fixture work',acceptance_criteria=['Verified'],**extra))['id']

    def offer(self,p,actor,**extra):
        return run('worker_launch',dict(project=p,actor=actor,**extra))

    def test_empty_and_dependency_blocked_work_do_not_launch(self):
        p,_=self.project()
        self.assertFalse(self.offer(p,self.agent)['eligible'])
        prerequisite=self.task(p,'Prerequisite')
        self.task(p,'Dependent',dependencies=[prerequisite])
        run('task_claim',dict(project=p,id=prerequisite,actor=self.agent))
        other=self.worker(p,'other')
        self.assertFalse(self.offer(p,other)['eligible'])

    def test_concurrent_bridges_offer_one_task_to_one_worker(self):
        p,_=self.project();task=self.task(p,'Only task')
        actors=[self.agent,self.worker(p,'second')]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda actor:self.offer(p,actor),actors))
        self.assertEqual(sum(r.get('eligible',False) for r in results),1)
        self.assertFalse(run('task_get',dict(project=p,id=task))['locked'])
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'TODO')

    def test_disjoint_tasks_can_launch_but_overlapping_files_cannot(self):
        p,_=self.project()
        first=self.task(p,'First',files=['shared.py'])
        self.task(p,'Overlap',files=['shared.py'])
        independent=self.task(p,'Independent',files=['free.py'])
        other=self.worker(p,'other')
        self.assertEqual(self.offer(p,self.agent)['id'],first)
        self.assertEqual(self.offer(p,other)['id'],independent)

    def test_selection_respects_offers_and_release_makes_work_available(self):
        p,_=self.project();task=self.task(p,'Only task');other=self.worker(p,'other')
        self.offer(p,self.agent)
        self.assertIsNone(run('next_work',dict(project=p,actor=other))['task'])
        self.assertEqual(run('next_work',dict(project=p,actor=self.agent))['task']['id'],task)
        self.offer(p,self.agent,action='release')
        self.assertEqual(self.offer(p,other)['id'],task)

    def test_crashed_startup_offer_expires(self):
        p,_=self.project();task=self.task(p,'Only task');other=self.worker(p,'other')
        self.offer(p,self.agent)
        future=time.time()+601
        with patch('store.time.time',return_value=future):
            self.quota(p,other)
            self.assertEqual(self.offer(p,other)['id'],task)

    def test_quota_stop_and_actual_file_conflicts_prevent_start(self):
        p,_=self.project();first=self.task(p,'Held',files=['shared.py']);self.task(p,'Other',files=['shared.py'])
        run('task_claim',dict(project=p,id=first,actor=self.agent,files=['shared.py']))
        other=self.worker(p,'other')
        self.assertFalse(self.offer(p,other)['eligible'])
        run('agent_checkpoint',dict(project=p,actor=other,client='codex',session_used_percent=100,weekly_used_percent=20))
        self.assertTrue(self.offer(p,other)['should_stop'])
        self.assertTrue(self.offer(p,other,action='release')['released'])

    def test_actual_owner_can_resume_despite_advisory_offer_and_wip_is_enforced(self):
        p,_=self.project();task=self.task(p,'Actual work',files=['owned.py']);other=self.worker(p,'other')
        self.offer(p,other)
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent,files=['owned.py']))
        self.assertTrue(claim['claimed'])
        self.assertEqual(self.offer(p,self.agent)['id'],task)
        run('project_settings',dict(project=p,actor=self.human,max_active_per_agent=1))
        self.task(p,'Extra work')
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],blocked_reason='Needs a human decision'))
        self.assertFalse(self.offer(p,self.agent)['eligible'])
