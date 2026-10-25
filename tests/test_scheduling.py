import unittest
from unittest.mock import patch
import test_storage
from store import run
import scheduling


class SchedulingTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def task(self,p,title='Work',**fields):
        return run('task_create',dict(project=p,title=title,description='One concrete outcome',acceptance_criteria=['Verified'],**fields))['id']

    def test_urgent_dependency_and_fanout_outrank_old_resumed_work(self):
        p,_=self.project();old=self.task(p,'Old',priority=2)
        claim=run('task_claim',dict(project=p,id=old,actor=self.agent))
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='offline',summary='Resume partial work'))
        prerequisite=self.task(p,'Low priority prerequisite',priority=3)
        self.task(p,'Urgent dependent',priority=1,dependencies=[prerequisite])
        self.assertEqual(run('next_work',dict(project=p,actor=self.agent))['task']['id'],prerequisite)
        self.assertEqual(run('task_get',dict(project=p,id=prerequisite))['priority'],3)
        run('task_update',dict(project=p,id=prerequisite,actor=self.human,status='Done',evidence='Verified'))
        # The urgent dependent is now executable, ahead of old resumed work.
        chosen=run('next_work',dict(project=p,actor=self.agent))['task']
        self.assertEqual(chosen['priority'],1)

    def test_dependency_only_block_rechecked_without_overriding_external_or_locks(self):
        p,_=self.project();dep=self.task(p,'Prerequisite')
        pending=self.task(p,'Dependency-only',dependencies=[dep])
        external=self.task(p,'External',dependencies=[dep])
        run('task_update',dict(project=p,id=pending,actor=self.human,status='Blocked',blocked_reason='Waiting',blocked_reason_kind='dependency'))
        run('task_update',dict(project=p,id=external,actor=self.human,status='Blocked',blocked_reason='Physical review',blocked_reason_kind='external'))
        with patch('store.resolve_dependents',return_value=[]):
            run('task_update',dict(project=p,id=dep,actor=self.human,status='Done',evidence='Verified'))
        self.assertEqual(run('task_get',dict(project=p,id=pending))['status'],'Blocked')
        self.assertEqual(run('next_work',dict(project=p,actor=self.agent))['task']['id'],pending)
        self.assertEqual(run('task_get',dict(project=p,id=pending))['status'],'TODO')
        self.assertEqual(run('task_get',dict(project=p,id=external))['blocked_reason'],'Physical review')

    def test_human_review_is_not_an_agent_offer_or_claim(self):
        p,_=self.project();human=self.task(p,'Physical review',priority=1,category='Human review')
        normal=self.task(p,'Executable work',priority=2)
        self.assertEqual(run('worker_launch',dict(project=p,actor=self.agent))['id'],normal)
        with self.assertRaisesRegex(ValueError,'Human review'):
            run('task_claim',dict(project=p,id=human,actor=self.agent))
        with self.assertRaisesRegex(ValueError,'Human review'):
            run('task_update',dict(project=p,id=human,actor=self.agent,status='In Progress',claim=True))

    def test_keys_count_shared_descendants_once_and_skip_external_paths(self):
        def t(i,priority=2,deps=(),**extra):return dict(id=i,status='TODO',priority=priority,dependencies=list(deps),blocked_reason='',**extra)
        tasks=[t(1),t(2,deps=[1]),t(3,deps=[1]),t(4,1,[2,3])]
        self.assertEqual(scheduling.keys(tasks)[1][1:4],(1,-2,-3))
        tasks[1].update(blocked_reason='External constraint',blocked_reason_kind='external')
        tasks[2].update(blocked_reason='External constraint',blocked_reason_kind='external')
        self.assertEqual(scheduling.keys(tasks)[1][1:4],(2,0,0))

    def test_batch_yields_only_for_greater_dependency_urgency(self):
        p,_=self.project();first=self.task(p,'First',files=['shared.py'])
        second=self.task(p,'Next in batch',files=['shared.py'])
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[first,second])
        claim=run('task_claim',dict(project=p,id=first,actor=self.agent))
        outside=self.task(p,'Urgent prerequisite',priority=3,files=['other.py'])
        self.task(p,'Urgent blocked outcome',priority=1,dependencies=[first,outside])
        result=run('work_finish',dict(project=p,id=first,actor=self.agent,token=claim['token'],client='codex',use_batch=True,evidence='Verified'))
        self.assertTrue(result['next']['batch_done'])
        self.assertEqual(run('worker_launch',dict(project=p,actor=self.agent,client='codex'))['id'],outside)
