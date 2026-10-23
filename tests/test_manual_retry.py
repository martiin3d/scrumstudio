"""Human retries clear obsolete prose without dropping dependency gates."""
import unittest
import test_storage
from store import run


class ManualRetryTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def blocked(self,dependencies=None):
        project,_=self.project()
        task=run('task_create',dict(project=project,title='Retry work',description='Outcome',acceptance_criteria=['Verified'],dependencies=dependencies or []))['id']
        run('task_update',dict(project=project,id=task,actor=self.human,status='Blocked',blocked_reason='External service unavailable',continuity='Reuse retained partial work',blocked_reason_kind='external'))
        return project,task

    def test_human_retry_from_board_or_unchanged_edit_form_clears_old_reason(self):
        for extra in ({},{'blocked_reason':'External service unavailable','blocked_reason_kind':'external'}):
            with self.subTest(extra=extra):
                project,task=self.blocked()
                result=run('task_update',dict(project=project,id=task,actor=self.human,status='TODO',**extra))
                current=run('task_get',dict(project=project,id=task))
                self.assertEqual(result['status'],'TODO');self.assertEqual(current['blocked_reason'],'')
                self.assertEqual(current['continuity'],'Reuse retained partial work')
                self.assertTrue(run('task_claim',dict(project=project,id=task,actor=self.agent))['claimed'])
                history=run('task_activity',dict(project=project,id=task))['items']
                self.assertTrue(any('previous blocker cleared: External service unavailable' in item['text'] for item in history))

    def test_retry_retains_dependency_gate_then_becomes_claimable(self):
        project,_=self.project()
        dep=run('task_create',dict(project=project,title='Prerequisite',description='Outcome',acceptance_criteria=['Verified']))['id']
        task=run('task_create',dict(project=project,title='Dependent',description='Outcome',acceptance_criteria=['Verified'],dependencies=[dep]))['id']
        run('task_update',dict(project=project,id=task,actor=self.human,status='Blocked',blocked_reason='Await prerequisite',blocked_reason_kind='dependency'))
        run('task_update',dict(project=project,id=task,actor=self.human,status='TODO',blocked_reason='Await prerequisite',blocked_reason_kind='dependency'))
        current=run('task_get',dict(project=project,id=task))
        self.assertEqual(current['blocked_reason'],'');self.assertEqual(current['dependencies'],[dep])
        with self.assertRaises(ValueError):run('task_claim',dict(project=project,id=task,actor=self.agent))
        run('task_update',dict(project=project,id=dep,actor=self.human,status='Done',evidence='Verified prerequisite'))
        self.assertTrue(run('task_claim',dict(project=project,id=task,actor=self.agent))['claimed'])

    def test_agent_retry_does_not_silently_clear_external_reason(self):
        project,task=self.blocked()
        run('task_update',dict(project=project,id=task,actor=self.agent,status='TODO'))
        self.assertEqual(run('task_get',dict(project=project,id=task))['blocked_reason'],'External service unavailable')
        with self.assertRaises(ValueError):run('task_claim',dict(project=project,id=task,actor=self.agent))

    def test_different_new_reason_is_not_silently_discarded(self):
        project,task=self.blocked()
        with self.assertRaises(ValueError):run('task_update',dict(project=project,id=task,actor=self.human,status='TODO',blocked_reason='A new unresolved constraint'))
        self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'Blocked')
