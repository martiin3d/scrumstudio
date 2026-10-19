from concurrent.futures import ThreadPoolExecutor
import json
import unittest
import test_storage
from store import run

class CapacityTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    clone=test_storage.StorageTest.clone

    def project(self):
        project,path=test_storage.StorageTest.project(self)
        run('project_settings',dict(project=project,actor=self.human,max_parallel_agents=1))
        return project,path

    def worker(self,p):
        actor=run('user_register',dict(identity='agent:capacity:'+str(len(run('users',{}))),name='Capacity worker',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=actor,client='codex',session_used_percent=10,weekly_used_percent=20))
        return actor

    def task(self,p):
        return run('task_create',dict(project=p,title='Independent',description='Bounded outcome',acceptance_criteria=['Verified']))['id']

    def test_default_and_human_policy(self):
        folder=self.root/'default';folder.mkdir()
        p=run('project_create',dict(path=str(folder),name='Default'))['id']
        self.assertEqual(run('project_settings',dict(project=p))['max_parallel_agents'],1)
        with self.assertRaises(ValueError):run('project_settings',dict(project=p,actor=self.agent,max_parallel_agents=2))
        for bad in [0,21,True]:
            with self.assertRaises(ValueError):run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=bad))

    def test_racing_offers_and_claims_cannot_exceed_one_agent(self):
        p,_=self.project();other=self.worker(p)
        first,second=self.task(p),self.task(p)
        def offer(actor):return run('worker_launch',dict(project=p,actor=actor))
        with ThreadPoolExecutor(max_workers=2) as pool:offers=list(pool.map(offer,[self.agent,other]))
        self.assertEqual(sum(bool(result.get('eligible')) for result in offers),1)
        winner=self.agent if offers[0].get('eligible') else other
        loser=other if winner==self.agent else self.agent
        chosen=offers[0 if winner==self.agent else 1]['id']
        claim=run('task_claim',dict(project=p,id=chosen,actor=winner))
        with self.assertRaises(ValueError):run('task_claim',dict(project=p,id=second if chosen==first else first,actor=loser))
        self.assertIsNone(run('next_work',dict(project=p,actor=loser))['task'])
        run('task_release',dict(project=p,id=chosen,actor=winner,token=claim['token']))
        # The supervisor's slot remains until process-exit cleanup, not just task release.
        self.assertFalse(offer(loser)['eligible'])
        run('worker_launch',dict(project=p,actor=winner,action='release'))
        self.assertTrue(offer(loser)['eligible'])

    def test_lowering_limit_preserves_active_claims_and_portable_policy(self):
        p,path=self.project();other=self.worker(p)
        run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=2))
        tasks=[self.task(p),self.task(p)]
        claims=[run('task_claim',dict(project=p,id=task,actor=actor)) for task,actor in zip(tasks,[self.agent,other])]
        run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=1))
        self.assertTrue(all(run('task_get',dict(project=p,id=task))['locked'] for task in tasks))
        for task,actor,claim in zip(tasks,[self.agent,other],claims):
            self.assertTrue(run('task_claim',dict(project=p,id=task,actor=actor,token=claim['token']))['claimed'])
        third=self.worker(p)
        self.assertFalse(run('worker_launch',dict(project=p,actor=third))['eligible'])
        run('project_export',dict(project=p))
        clone,_=self.clone(path)
        self.assertEqual(run('project_settings',dict(project=clone['id']))['max_parallel_agents'],1)
        snapshot=path/'.scrum/snapshot.json';data=json.loads(snapshot.read_text(encoding='utf-8'));data['settings'].pop('max_parallel_agents');snapshot.write_text(json.dumps(data),encoding='utf-8')
        legacy,_=self.clone(path)
        self.assertEqual(run('project_settings',dict(project=legacy['id']))['max_parallel_agents'],1)
