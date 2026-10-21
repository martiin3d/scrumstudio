import unittest
import test_storage
from store import run


class RecoveryReadinessTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def test_recovery_offer_skips_external_constraints_and_unfinished_prerequisites(self):
        project,_=self.project()
        def task(title,**extra):
            return run('task_create',dict(project=project,title=title,description='Recover one concrete Git preparation failure',acceptance_criteria=['Preparation works'],**extra))['id']
        prerequisite=task('Prerequisite')
        waiting=task('Waiting recovery',category='Git recovery',dependencies=[prerequisite])
        external=task('External recovery',category='Git recovery')
        run('task_update',dict(project=project,id=external,blocked_reason='Account authorization needed'))
        ready=task('Ready recovery',category='Git recovery')
        self.assertEqual(run('git_recovery_next',dict(project=project)),{'id':ready})
        claim=run('task_claim',dict(project=project,id=ready,actor=self.agent))
        self.assertIsNone(run('git_recovery_next',dict(project=project)))
        run('work_finish',dict(project=project,id=ready,actor=self.agent,token=claim['token'],evidence='Recovered'))
        self.assertIsNone(run('git_recovery_next',dict(project=project)))
        run('task_update',dict(project=project,id=prerequisite,actor=self.human,status='Done',evidence='Verified prerequisite'))
        self.assertEqual(run('git_recovery_next',dict(project=project)),{'id':waiting})
