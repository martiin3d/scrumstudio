import unittest
import test_app
from store import run
from mcp_server import handle

class TaskSpecificationTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def test_incomplete_creation_rejected_without_consuming_number(self):
        base={'project':self.p,'title':'Atomic specification','description':'Deliver a validated task specification.','acceptance_criteria':['Invalid requests create no rows.']}
        for fields in [{'description':''},{'description':'  '},{'acceptance_criteria':[]},{'acceptance_criteria':[' ']},{'acceptance_criteria':'not a list'}]:
            with self.assertRaises(ValueError):run('task_create',dict(base,**fields))
        incomplete=dict(base);incomplete.pop('acceptance_criteria')
        with self.assertRaises(ValueError):run('task_create',incomplete)
        self.assertEqual(run('snapshot',{'project':self.p})['tasks'],[])
        saved=run('task_create',base)
        self.assertEqual(saved['code'],'PRO-1')
        task=run('task_get',{'project':self.p,'id':saved['id']})
        self.assertEqual(task['description'],base['description'])
        self.assertEqual(task['acceptance_criteria'],base['acceptance_criteria'])
        run('task_activity',{'project':self.p,'id':saved['id'],'actor':self.actor,'kind':'comment','text':'Verified creation guard; checking UI next.'})
        self.assertEqual(run('task_get',{'project':self.p,'id':saved['id']})['description'],base['description'])

    def test_mcp_requires_spec_in_initial_request(self):
        response=handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'task_create','arguments':{'project':self.p,'title':'Missing spec'}}})
        self.assertTrue(response['result']['isError'])

    def test_verification_intent_validation_persistence_and_context(self):
        base=dict(project=self.p,title='Verification intent',description='Persist optional intent.',acceptance_criteria=['Intent survives reads.'])
        plain=run('task_create',base)['id']
        self.assertNotIn('verification_level',run('task_get',dict(project=self.p,id=plain)))
        self.assertEqual(run('task_get',dict(project=self.p,id=plain,full=True))['verification_level'],'auto')
        for level in ('auto','minimal','targeted','full'):
            saved=run('task_create',dict(base,verification_level=level,verification_reason='Check affected behavior.'))['id']
            task=run('task_get',dict(project=self.p,id=saved))
            self.assertEqual(task['verification_level'],level)
            self.assertEqual(task['verification_reason'],'Check affected behavior.')
        for fields in (dict(verification_level='bad'),dict(verification_level=[]),dict(verification_level='minimal'),dict(verification_reason=3),dict(verification_reason='x'*501)):
            with self.assertRaises(ValueError):run('task_create',dict(base,**fields))
            with self.assertRaises(ValueError):run('task_update',dict(project=self.p,id=plain,actor=self.actor,**fields))
        run('task_update',dict(project=self.p,id=plain,actor=self.actor,verification_level='full',verification_reason='Shared storage contract.'))
        self.assertEqual(run('task_get',dict(project=self.p,id=plain))['verification_level'],'full')
        with self.assertRaises(ValueError):run('task_update',dict(project=self.p,id=plain,actor=self.actor,status='Done',evidence=''))
        run('task_update',dict(project=self.p,id=plain,actor=self.actor,verification_level='auto',verification_reason=''))
        self.assertNotIn('verification_level',run('task_get',dict(project=self.p,id=plain)))

    def test_verification_mcp_and_atomic_planning(self):
        from mcp_server import TOOLS
        schemas={name:spec for name,_,spec in TOOLS}
        for name in ('task_create','task_update'):
            self.assertIn('verification_level',schemas[name]['properties'])
        item=dict(key='intent',title='Planned intent',description='Plan verification with scope.',acceptance_criteria=['Saved atomically.'],verification_level='targeted',verification_reason='Affected behavior regression.')
        result=run('task_plan',dict(project=self.p,actor=self.actor,items=[item]))
        task=run('task_get',dict(project=self.p,id=result['items'][0]['id']))
        self.assertEqual(task['verification_level'],'targeted')
        with self.assertRaises(ValueError):run('task_plan',dict(project=self.p,actor=self.actor,items=[dict(item,key='valid'),dict(item,key='invalid',verification_reason='')]))
        self.assertEqual(len(run('snapshot',dict(project=self.p))['tasks']),1)


class VerificationPortableTest(unittest.TestCase):
    from test_storage import StorageTest as _Fixture
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    project=_Fixture.project
    clone=_Fixture.clone

    def test_portable_intent_and_legacy_defaults(self):
        import json
        p,path=self.project()
        run('task_create',dict(project=p,title='Portable intent',description='Preserve verification intent.',acceptance_criteria=['Round trip matches.'],verification_level='full',verification_reason='Storage compatibility.'))
        run('project_export',dict(project=p))
        opened,_=self.clone(path)
        task=run('snapshot',dict(project=opened['id']))['tasks'][0]
        self.assertEqual((task['verification_level'],task['verification_reason']),('full','Storage compatibility.'))
        snapshot=path/'.scrum/snapshot.json'
        data=json.loads(snapshot.read_text(encoding='utf-8'))
        for row in data['tables']['tasks']:
            row.pop('verification_level');row.pop('verification_reason')
        snapshot.write_text(json.dumps(data),encoding='utf-8')
        legacy,_=self.clone(path)
        task=run('snapshot',dict(project=legacy['id']))['tasks'][0]
        self.assertEqual((task['verification_level'],task['verification_reason']),('auto',''))
