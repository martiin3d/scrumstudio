import unittest
from store import run
import test_storage
import mcp_server


class BacklogTest(unittest.TestCase):
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def setUp(self):
        test_storage.StorageTest.setUp(self)
        self.p,self.folder=self.project()

    tearDown=test_storage.StorageTest.tearDown

    def create(self,title='Parked work',**kw):
        return run('task_create',dict(project=self.p,actor=self.human,title=title,description='A bounded requested outcome',acceptance_criteria=['Verified'],status='Backlog',**kw))['id']

    def get(self,id):
        return run('task_get',dict(project=self.p,id=id))

    def test_create_and_human_activation_preserve_history(self):
        id=self.create()
        self.assertEqual(self.get(id)['status'],'Backlog')
        self.assertIsNone(run('next_work',dict(project=self.p,actor=self.agent))['task'])
        run('task_update',dict(project=self.p,id=id,actor=self.human,status='TODO'))
        self.assertEqual(run('next_work',dict(project=self.p,actor=self.agent))['task']['id'],id)
        claim=run('task_claim',dict(project=self.p,id=id,actor=self.agent))
        self.assertTrue(claim['claimed'])
        timing=run('task_timing',dict(project=self.p,id=id,history=True))
        self.assertIn('Backlog',[v['status'] for v in timing['intervals']])

    def test_agents_cannot_claim_promote_or_create_parked_work(self):
        id=self.create()
        for action,args in [('task_claim',{}),('task_update',{'claim':True}),('task_update',{'status':'TODO'}),('task_update',{'status':'In Progress'})]:
            with self.assertRaisesRegex(ValueError,'Backlog'):
                run(action,dict(project=self.p,id=id,actor=self.agent,**args))
            self.assertEqual(self.get(id)['status'],'Backlog')
        with self.assertRaisesRegex(ValueError,'human'):
            run('task_create',dict(project=self.p,actor=self.agent,title='Agent parked',description='Request',acceptance_criteria=['Checked'],status='Backlog'))

    def test_human_must_activate_before_starting(self):
        id=self.create()
        with self.assertRaisesRegex(ValueError,'TODO'):
            run('task_claim',dict(project=self.p,id=id,actor=self.human))
        with self.assertRaisesRegex(ValueError,'TODO'):
            run('task_update',dict(project=self.p,id=id,actor=self.human,status='Done',evidence='Not executed'))
        self.assertEqual(self.get(id)['status'],'Backlog')

    def test_dependency_completion_and_cancellation_leave_backlog_parked(self):
        for terminal in ['Done','Cancelled']:
            prerequisite=run('task_create',dict(project=self.p,actor=self.human,title='Prerequisite '+terminal,description='Request',acceptance_criteria=['Checked']))['id']
            parked=self.create(title='Waiting '+terminal,dependencies=[prerequisite])
            run('task_update',dict(project=self.p,id=prerequisite,actor=self.human,status=terminal,evidence='Verified',cancel_reason='No longer needed'))
            self.assertEqual(self.get(parked)['status'],'Backlog')
        self.assertIsNone(run('next_work',dict(project=self.p,actor=self.agent))['task'])
        launch=run('worker_launch',dict(project=self.p,actor=self.agent,client='codex'))
        self.assertFalse(launch.get('eligible',False))

    def test_human_can_park_todo_but_agent_cannot(self):
        id=run('task_create',dict(project=self.p,title='Ready',description='Request',acceptance_criteria=['Checked']))['id']
        with self.assertRaisesRegex(ValueError,'human'):
            run('task_update',dict(project=self.p,id=id,actor=self.agent,status='Backlog'))
        run('task_update',dict(project=self.p,id=id,actor=self.human,status='Backlog'))
        self.assertEqual(self.get(id)['status'],'Backlog')

    def test_human_batch_plans_can_create_backlog_without_changing_existing_state(self):
        item=dict(key='park',title='Planned parked',description='Request',acceptance_criteria=['Checked'],status='Backlog')
        result=run('task_plan',dict(project=self.p,actor=self.human,items=[item]))
        self.assertEqual(result['items'][0]['status'],'Backlog')
        with self.assertRaisesRegex(ValueError,'human'):
            run('task_plan',dict(project=self.p,actor=self.agent,items=[dict(item,key='other',title='Other')]))
        with self.assertRaisesRegex(ValueError,'task_update'):
            run('task_plan',dict(project=self.p,actor=self.human,items=[dict(item,id=result['items'][0]['id'],status='TODO')]))

    def test_portable_snapshot_keeps_backlog(self):
        self.create()
        run('project_export',dict(project=self.p))
        opened,_=self.clone(self.folder)
        self.assertEqual(run('snapshot',dict(project=opened['id']))['tasks'][0]['status'],'Backlog')

    def test_mcp_schemas_expose_manual_backlog(self):
        schemas={name:schema for name,_,schema in mcp_server.TOOLS}
        self.assertIn('Backlog',schemas['task_create']['properties']['status']['enum'])
        self.assertIn('Backlog',schemas['task_update']['properties']['status']['enum'])
        self.assertIn('Backlog',schemas['task_plan']['properties']['items']['items']['properties']['status']['enum'])
