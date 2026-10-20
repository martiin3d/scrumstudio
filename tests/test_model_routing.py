import json
import unittest
from unittest.mock import patch
import test_storage
from store import run
import model_routing

class RoutingTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def task(self,p,**extra):
        return run('task_create',dict(project=p,title='Route task',description='Bounded outcome',acceptance_criteria=['Verified'],**extra))['id']

    def test_auto_precedence_and_cross_provider(self):
        available={'gpt-6-luna','gpt-6.1-sol','gpt-6-astra'}
        self.assertEqual(model_routing.select({'scope_size':'Small'},'codex',available=available)['model'],'gpt-6.1-sol')
        self.assertEqual(model_routing.select({},'codex',available=available)['model'],'gpt-6.1-sol')
        self.assertEqual(model_routing.select({'scope_size':'Large'},'codex',available=available)['model'],'gpt-6.1-sol')
        self.assertEqual(model_routing.select({'scope_size':'Small','model_tier':'advanced','model_reason':'Subtle recovery invariants'},'codex',available=available)['model'],'gpt-6-astra')
        self.assertEqual(model_routing.select({'scope_size':'Large','model_tier':'economy','model_reason':'Mechanical repeated edits'},'codex',available=available)['model'],'gpt-6-luna')
        for scope in ['Small','Large','Unknown']:
            self.assertEqual(model_routing.select({'scope_size':scope},'claude')['model'],'sonnet')
        self.assertEqual(model_routing.select({'model':'fixed-task'},'codex','fixed-profile',available)['model'],'fixed-task')
        self.assertEqual(model_routing.select({},'codex','fixed-profile',available)['source'],'profile')
        self.assertEqual(model_routing.select({'model_tier':'economy','model_reason':'Mechanical change'},'claude')['model'],'haiku')
        with self.assertRaises(ValueError):model_routing.select({'model':'claude-other'},'codex')
        self.assertEqual(model_routing.select({},'codex',available=set())['model'],'')

    def test_human_lock_override_agent_tier_and_portable_models(self):
        p,folder=self.project()
        task=self.task(p)
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],model_tier='advanced',model_reason='Complex invariant'))
        run('task_update',dict(project=p,id=task,actor=self.human,model='fixed-task'))
        for change in [{'model':'other'},{'model_tier':'economy','model_reason':'Cheaper'}]:
            with self.assertRaises(ValueError):run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],**change))
        with patch('model_routing.catalog',return_value={'gpt-6-luna'}):
            offered=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual(offered['selection']['model'],'fixed-task')
        self.assertEqual(run('task_get',dict(project=p,id=task))['model_selection']['source'],'task')
        run('project_export',dict(project=p))
        clone,_=self.clone(folder)
        restored=run('task_get',dict(project=clone['id'],code='PRO-1'))
        self.assertEqual(restored['model'],'fixed-task')
        # Legacy exports lack all model-routing columns.
        path=folder/'.scrum/snapshot.json';data=json.loads(path.read_text(encoding='utf-8'))
        for row in data['tables']['tasks']:
            for key in model_routing.FIELDS:row.pop(key)
        path.write_text(json.dumps(data),encoding='utf-8')
        legacy,_=self.clone(folder)
        self.assertEqual(run('task_get',dict(project=legacy['id'],code='PRO-1'))['model_tier'],'auto')
