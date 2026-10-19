import unittest
import json
import test_app
import test_storage
from store import run

class CostTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown
    def task(self):
        return run('task_create',dict(project=self.p,actor=self.actor,title='Measured',description='Bounded change',acceptance_criteria=['Verified'],estimated_tokens=100,estimated_quota_percent=1.5))['id']
    def test_unknown_and_deduplicated_runs(self):
        t=self.task()
        self.assertIsNone(run('task_get',dict(include_cost=True,project=self.p,id=t))['cost_summary']['reported_tokens'])
        report=dict(run_id='task-run',source='client-telemetry',input_tokens=40,output_tokens=10)
        args=dict(project=self.p,id=t,actor=self.actor,usage_report=report)
        run('task_update',args);run('task_update',args)
        self.assertEqual(run('task_get',dict(include_cost=True,project=self.p,id=t))['cost_summary']['reported_tokens'],50)
        second=run('user_register',dict(identity='agent:other-cost',name='Other',kind='agent'))['id']
        run('task_update',dict(args,actor=second))
        self.assertEqual(run('task_get',dict(include_cost=True,project=self.p,id=t))['cost_summary']['reported_tokens'],100)
        with self.assertRaises(ValueError):run('task_update',dict(args,usage_report=dict(report,input_tokens=1)))
        with self.assertRaises(ValueError):run('task_update',dict(args,usage_report=dict(report,output_tokens=True)))
        run('task_update',dict(project=self.p,id=t,actor=self.actor,status='Cancelled',cancel_reason='No longer needed',usage_report=dict(report,input_tokens=45)))
        result=run('task_get',dict(include_cost=True,project=self.p,id=t))
        self.assertEqual(result['cost_summary']['reported_tokens'],105)
        self.assertEqual(result['cost_summary']['token_variance'],5)
    def test_completion_records_usage_in_same_call(self):
        t=self.task()
        claim=run('task_claim',dict(project=self.p,id=t,actor=self.actor))
        result=run('work_finish',dict(project=self.p,id=t,actor=self.actor,token=claim['token'],evidence='Verified result',usage_report=dict(run_id='completion',source='client',input_tokens=80,output_tokens=20)))
        task=run('task_get',dict(include_cost=True,project=self.p,id=t))
        self.assertEqual(task['status'],'Done')
        self.assertEqual(task['cost_summary']['reported_tokens'],100)
        self.assertNotIn('reports',task['cost_data'])

    def test_invalid_estimate_rolls_back(self):
        t=self.task()
        for val in (-1,True,float('nan'),101):
            with self.assertRaises(ValueError):run('task_update',dict(project=self.p,id=t,actor=self.actor,estimated_quota_percent=val))
        self.assertEqual(run('task_get',dict(include_cost=True,project=self.p,id=t))['cost_data']['estimated_quota_percent'],1.5)

class PortableCostTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone
    def test_portable_cost_and_old_defaults(self):
        p,folder=self.project()
        t=run('task_create',dict(project=p,actor=self.agent,title='Portable cost',description='Portable totals',acceptance_criteria=['Restored'],estimated_tokens=50))['id']
        run('task_update',dict(project=p,id=t,actor=self.agent,usage_report=dict(run_id='one',source='client',input_tokens=20,output_tokens=5)))
        run('project_export',dict(project=p))
        cloned,_=self.clone(folder)
        task=run('task_get',dict(project=cloned['id'],code='PRO-1',include_cost=True))
        self.assertEqual(task['cost_summary']['reported_tokens'],25)
        path=folder/'.scrum/snapshot.json'
        data=json.loads(path.read_text(encoding='utf-8'))
        for row in data['tables']['tasks']:row.pop('cost_data')
        path.write_text(json.dumps(data),encoding='utf-8')
        old,_=self.clone(folder)
        self.assertIsNone(run('task_get',dict(project=old['id'],code='PRO-1',include_cost=True))['cost_summary']['reported_tokens'])
