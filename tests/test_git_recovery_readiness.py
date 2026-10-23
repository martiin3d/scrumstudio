import unittest
from unittest.mock import patch
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


    def checkout_block(self,kind='checkout',dependencies=None):
        project,folder=self.project()
        run('project_settings',dict(project=project,actor=self.human,git_mode='Branches'))
        spec=dict(project=project,title='Checkout recovery',category='Git recovery',description='Release checkout obstruction',acceptance_criteria=['Ready work can proceed'])
        task=run('task_create',spec)['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
        args=dict(project=project,id=task,actor=self.agent,token=claim['token'],status='Blocked',blocked_reason='Dirty checkout',blocked_reason_kind=kind,continuity='Preserve prior investigation; verify checkout before closing.')
        if dependencies:args['dependencies']=dependencies(project)
        run('task_update',args)
        return project,task

    def test_clean_checkout_reopens_for_verification_without_assuming_done(self):
        project,task=self.checkout_block()
        with patch('git_sync.state',return_value=('main',[])):
            run('next_work',dict(project=project,actor=self.agent))
            self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'Blocked')
            offer=run('worker_launch',dict(project=project,actor=self.agent,client='codex'))
            self.assertTrue(offer['eligible']);self.assertEqual(offer['id'],task)
            run('worker_launch',dict(project=project,actor=self.agent,client='codex'))
        task=run('task_get',dict(project=project,id=task))
        self.assertEqual(task['status'],'TODO');self.assertFalse(task['blocked_reason'])
        self.assertIn('Preserve prior investigation',task['continuity']);self.assertFalse(task['evidence'])

    def test_dirty_unknown_external_and_stop_are_not_cleared(self):
        for case in ('dirty','unavailable','external','stop'):
            with self.subTest(case=case):
                project,task=self.checkout_block('external' if case=='external' else 'checkout')
                if case=='stop':run('project_control',dict(project=project,actor=self.human,action='stop'))
                state=None if case=='unavailable' else ('main',['uncommitted'] if case=='dirty' else [])
                with patch('git_sync.state',return_value=state):
                    run('worker_launch',dict(project=project,actor=self.agent,client='codex'))
                self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'Blocked')

    def test_checkout_reopen_keeps_unfinished_prerequisites(self):
        def dependencies(project):
            return [run('task_create',dict(project=project,title='Prerequisite',description='Required',acceptance_criteria=['Verified']))['id']]
        project,task=self.checkout_block(dependencies=dependencies)
        with patch('git_sync.state',return_value=('main',[])):
            run('worker_launch',dict(project=project,actor=self.agent,client='codex'))
        self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'Blocked')

    def test_checkout_kind_cannot_clear_an_ordinary_task(self):
        project,_=self.project()
        task=run('task_create',dict(project=project,title='Ordinary',description='Implementation',acceptance_criteria=['Verified']))['id']
        with self.assertRaisesRegex(ValueError,'Git recovery'):
            run('task_update',dict(project=project,id=task,actor=self.human,status='Blocked',blocked_reason='Not a checkout recovery',blocked_reason_kind='checkout'))


    def test_checkout_blocker_survives_portable_export_and_import(self):
        project,folder=self.project()
        task=run('task_create',dict(project=project,title='Portable recovery',category='Git recovery',description='Preserve typed recovery',acceptance_criteria=['Verified']))
        run('task_update',dict(project=project,id=task['id'],actor=self.human,status='Blocked',blocked_reason='Checkout occupied',blocked_reason_kind='checkout'))
        run('project_export',dict(project=project))
        opened,clone=test_storage.StorageTest.clone(self,folder)
        imported=run('task_get',dict(project=opened['id'],code=task['code']))
        self.assertEqual(imported['status'],'Blocked')
        self.assertEqual(imported['blocked_reason_kind'],'checkout')
