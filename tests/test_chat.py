import unittest
import test_app
from store import run
import mcp_server

class ChatTest(unittest.TestCase):
    setUp = test_app.WorkflowTest.setUp
    tearDown = test_app.WorkflowTest.tearDown
    task = test_app.WorkflowTest.task

    def test_isolation_and_pagination(self):
        ids = [run('project_chat', dict(project=self.p, actor=self.actor, text=f'Update {i}'))['id'] for i in range(4)]
        run('project_chat', dict(project=self.q, actor=self.actor, text='Other project'))
        latest = run('project_chat', dict(project=self.p, limit=2))
        self.assertEqual([m['id'] for m in latest['items']], ids[2:])
        self.assertTrue(latest['has_more'])
        older = run('project_chat', dict(project=self.p, before=latest['oldest']))
        self.assertEqual([m['id'] for m in older['items']], ids[:2])
        first = run('project_chat', dict(project=self.p, after=0, limit=2))
        second = run('project_chat', dict(project=self.p, after=first['latest']))
        self.assertEqual([m['id'] for m in second['items']], ids[2:])
        self.assertEqual(run('project_chat', dict(project=self.p, after=ids[-1]))['latest'], ids[-1])

    def test_authorship_validation_and_mcp(self):
        task = self.task('Coordinate')
        saved = run('project_chat', dict(project=self.p, actor=self.actor, recipient=self.actor, task=task, text='Need API contract'))
        message = run('project_chat', dict(project=self.p))['items'][0]
        self.assertEqual(message['author_name'], 'Test agent')
        self.assertTrue(message['created'])
        self.assertEqual(message['id'], saved['id'])
        for fields in [dict(actor=99999), dict(recipient=99999), dict(text=' '), dict(text='x'*2001), dict(task=task, project=self.q)]:
            with self.assertRaises(ValueError):
                run('project_chat', dict(dict(project=self.p, actor=self.actor, text='Question'), **fields))
        for fields in [dict(limit=0), dict(after=-1), dict(after=0,before=1)]:
            with self.assertRaises(ValueError):
                run('project_chat', dict(project=self.p, **fields))
        result = mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'project_chat','arguments':{'project':self.p,'after':0}}})
        self.assertNotIn('isError', result['result'])
