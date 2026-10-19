import json
import io
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import run

class WorkflowTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'data'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.previous = os.environ.get('SCRUM_DB')
        os.environ['SCRUM_DB'] = str(Path(self.temp.name) / 'test.db')
        self.p = run('project_create', {'name': 'Product'})['id']
        self.q = run('project_create', {'name': 'Other'})['id']
        self.actor = run('user_register', {'identity':'agent:test','name':'Test agent','kind':'agent'})['id']
        self.tokens = {}

    def tearDown(self):
        if self.previous is None:
            os.environ.pop('SCRUM_DB', None)
        else:
            os.environ['SCRUM_DB'] = self.previous
        self.temp.cleanup()

    def task(self, title):
        return run('task_create', {'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project': self.p, 'title': title})['id']

    def update(self, id, **kw):
        if kw.get('status') == 'Done':
            kw.setdefault('evidence','Relevant acceptance checks passed for this test fixture.')
        if kw.get('status') == 'In Progress':
            claim=run('task_claim', dict(project=self.p, id=id, actor=self.actor, token=self.tokens.get(id,'')))
            self.tokens[id]=claim['token']
        return run('task_update', dict(project=self.p, id=id, actor=self.actor, token=self.tokens.get(id,''), **kw))

    def test_selection_and_blockers(self):
        first, second, third = [self.task(s) for s in ['First', 'Second', 'Third']]
        self.update(second, status='Blocked', dependencies=[first])
        self.update(third, status='In Progress')
        self.assertEqual(run('next_work', {'project': self.p, 'actor':self.actor})['task']['id'], third)
        self.update(third, status='Done')
        self.assertEqual(run('next_work', {'project': self.p})['task']['id'], first)
        with self.assertRaises(ValueError):
            self.update(second, status='In Progress')
        self.update(first, status='Done')
        self.assertEqual(run('next_work', {'project': self.p})['task']['id'], second)
        self.update(second, blocked_reason='Waiting for approval')
        self.assertIsNone(run('next_work', {'project': self.p})['task'])
        self.update(second, blocked_reason='')
        self.update(second, status='In Review')
        self.assertIsNone(run('next_work', {'project': self.p})['task'])

    def test_isolation_validation_and_persistence(self):
        t = self.task('Persisted')
        self.assertEqual(run('snapshot', {'project': self.p})['tasks'][0]['title'], 'Persisted')
        self.assertEqual(run('snapshot', {'project': self.q})['tasks'], [])
        with self.assertRaises(ValueError):
            run('task_update', {'project': self.q, 'id': t, 'status': 'Done'})
        for args in [{'status':'Wrong'}, {'status':'Blocked'}, {'priority':9}, {'dependencies':[t]}]:
            with self.assertRaises(ValueError):
                self.update(t, **args)
        other=self.task('Other')
        self.update(t, dependencies=[other])
        with self.assertRaises(ValueError):
            self.update(other, dependencies=[t])

    def test_notes_and_retro_cache(self):
        r=run('note_add', {'project':self.p,'kind':'Retrospective','title':'Session','items':['Use smaller checks.']})['id']
        self.assertNotIn('retrospective',run('next_work', {'project':self.p}))
        self.assertNotIn('retrospective',run('next_work', {'project':self.p,'retro_seen':r}))
        n=run('note_add', {'project':self.p,'kind':'Review','title':'Verify','items':['Check layout']})['id']
        run('note_check', {'project':self.p,'id':n,'done':True})
        self.assertEqual(run('snapshot', {'project':self.p})['notes'][0]['done'], 1)
        with self.assertRaises(ValueError):
            run('note_check', {'project':self.q,'id':n,'done':True})

    def test_stdio_protocol(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}, {'jsonrpc':'2.0','method':'notifications/initialized'}, {'jsonrpc':'2.0','id':2,'method':'tools/list'}, {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'next_work','arguments':{'project':self.p}}}, {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'task_create','arguments':{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p,'title':'Via MCP'}}}]
        p=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'mcp_server.py')],input='\n'.join(json.dumps(r) for r in requests)+'\n',capture_output=True,text=True,check=True)
        replies=[json.loads(s) for s in p.stdout.splitlines()]
        self.assertEqual(len(replies),4)
        self.assertEqual(replies[0]['result']['serverInfo']['name'],'scrum-studio')
        tool_names=[t['name'] for t in replies[1]['result']['tools']]
        self.assertEqual(len(tool_names),len(set(tool_names)))
        self.assertTrue({'project_chat','task_plan','task_create','next_work'}.issubset(tool_names))
        self.assertIsNone(json.loads(replies[2]['result']['content'][0]['text'])['task'])
        self.assertEqual(run('snapshot', {'project':self.p})['tasks'][0]['title'],'Via MCP')

    def test_checklist_roundtrip_and_validation(self):
        from mcp_server import handle
        items = [{'text': 'Implement feature', 'done': False}, {'text': 'Verify results', 'done': False}]
        response = handle({'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{'name':'task_create', 'arguments':{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p, 'title':'With steps', 'checklist':items}}})
        self.assertNotIn('isError', response['result'])
        t = json.loads(response['result']['content'][0]['text'])['id']
        self.assertEqual(run('task_get', {'project':self.p,'id':t})['checklist'], items)
        items[0]['done'] = True
        response = handle({'jsonrpc':'2.0', 'id':2, 'method':'tools/call', 'params':{'name':'task_update', 'arguments':{'project':self.p, 'id':t, 'checklist':items}}})
        self.assertNotIn('isError', response['result'])
        self.update(t, priority=1, status='In Progress')
        self.assertEqual(run('snapshot', {'project':self.p})['tasks'][0]['checklist'], items)
        with self.assertRaises(ValueError):
            run('task_update', {'project':self.q, 'id':t, 'checklist':[]})
        for invalid in [[{'text':'', 'done':False}], [{'text':'x', 'done':1}], [{}], 'bad', [{'text':'x','done':False}]*101]:
            with self.assertRaises(ValueError):
                self.update(t, checklist=invalid)
        self.assertEqual(run('snapshot', {'project':self.p})['tasks'][0]['checklist'], items)
        self.update(t, checklist=[])
        self.assertEqual(run('snapshot', {'project':self.p})['tasks'][0]['checklist'], [])

    def test_existing_database_migration(self):
        # Recreate the previous release's task schema with a real existing record.
        db=sqlite3.connect(os.environ['SCRUM_DB'])
        db.execute('DROP TABLE tasks')
        db.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, project INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'TODO', priority INTEGER NOT NULL DEFAULT 2, blocked_reason TEXT NOT NULL DEFAULT '', dependencies TEXT NOT NULL DEFAULT '[]', updated TEXT NOT NULL)")
        db.execute("INSERT INTO tasks(project,title,updated) VALUES(?, 'Existing task', '2026-10-17')", (self.p,))
        db.commit()
        db.close()
        task=run('snapshot', {'project':self.p})['tasks'][0]
        self.assertEqual(task['title'], 'Existing task')
        self.assertEqual(task['checklist'], [])
        self.assertEqual(task['code'], 'PRO-1')
        self.update(task['id'], checklist=[{'text':'New step', 'done':True}])
        self.assertTrue(run('snapshot', {'project':self.p})['tasks'][0]['checklist'][0]['done'])

    def test_task_codes_per_project_and_stability(self):
        a=run('task_create', {'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p, 'title':'One'})
        b=run('task_create', {'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.q, 'title':'Other one'})
        c=run('task_create', {'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p, 'title':'Two'})
        self.assertEqual([a['code'],b['code'],c['code']], ['PRO-1','OTH-1','PRO-2'])
        self.assertNotEqual(b['id'], 1)
        self.assertEqual(self.update(a['id'], priority=3, status='Done')['code'], 'PRO-1')
        self.assertEqual(run('next_work', {'project':self.p})['task']['code'], 'PRO-2')
        self.assertEqual({t['code'] for t in run('snapshot', {'project':self.p})['tasks']}, {'PRO-1','PRO-2'})
        p=run('project_create', {'name':'  Web App'})['id']
        self.assertEqual(run('task_create', {'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Build'})['code'], 'WEB-1')

    def test_concurrent_task_numbers(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda i: run('task_create', {'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p,'title':f'Task {i}'}), range(8)))
        self.assertEqual({r['code'] for r in results}, {f'PRO-{i}' for i in range(1,9)})

    def test_claim_conflict_token_and_release(self):
        t=self.task('Shared')
        other=run('user_register', {'identity':'agent:other','name':'Test agent','kind':'agent'})['id']
        claim=run('task_claim', {'project':self.p,'id':t,'actor':self.actor})
        self.assertTrue(claim['token'])
        snap=run('snapshot', {'project':self.p})['tasks'][0]
        self.assertEqual(snap['owner_id'],self.actor)
        self.assertTrue(snap['locked'])
        self.assertNotIn('lock_token',snap)
        self.assertNotIn('token',snap)
        for args in [{'actor':other}, {'actor':self.actor}, {'actor':self.actor,'token':'wrong'}]:
            with self.assertRaises(ValueError):
                run('task_update', dict(project=self.p,id=t,title='Overwrite',**args))
            with self.assertRaises(ValueError):
                run('task_claim', dict(project=self.p,id=t,**args))
        self.assertIsNone(run('next_work', {'project':self.p,'actor':other})['task'])
        run('task_update', {'project':self.p,'id':t,'actor':self.actor,'token':claim['token'],'status':'Done','evidence':'Verified expected task output.'})
        snap=run('snapshot', {'project':self.p})['tasks'][0]
        self.assertFalse(snap['locked'])
        self.assertEqual(snap['owner_id'],self.actor)

    def test_claim_race_and_human_recovery(self):
        t=self.task('Race')
        agents=[run('user_register',{'identity':f'agent:race-{i}','name':f'Worker {i}','kind':'agent'})['id'] for i in range(4)]
        def claim(actor):
            try:
                return run('task_claim',{'project':self.p,'id':t,'actor':actor})
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(claim,agents))
        winners=[r for r in results if r]
        self.assertEqual(len(winners),1)
        with self.assertRaises(ValueError):
            run('task_release',{'project':self.p,'id':t,'actor':self.actor,'force':True})
        human=run('user_register',{'identity':'human:owner','name':'Owner','kind':'human'})['id']
        run('task_release',{'project':self.p,'id':t,'actor':human,'force':True})
        with self.assertRaises(ValueError):
            run('task_update',{'project':self.p,'id':t,'status':'In Progress'})
        fresh=run('task_claim',{'project':self.p,'id':t,'actor':self.actor})
        self.assertNotEqual(winners[0]['token'],fresh['token'])
        with self.assertRaises(ValueError):
            run('task_release',{'project':self.q,'id':t,'actor':human,'force':True})

    def test_identity_owner_and_expiry(self):
        same=run('user_register',{'identity':'agent:test','name':'Another display name','kind':'agent'})
        self.assertEqual(same['id'],self.actor)
        t=self.task('Expires')
        human=run('user_register',{'identity':'human:test','name':'Human','kind':'human'})['id']
        run('task_update',{'project':self.p,'id':t,'actor':human,'owner_id':human})
        self.assertEqual(run('snapshot',{'project':self.p})['tasks'][0]['owner_id'],human)
        a=run('task_claim',{'project':self.p,'id':t,'actor':self.actor,'lease_seconds':60})
        renewed=run('task_claim',{'project':self.p,'id':t,'actor':self.actor,'token':a['token']})
        self.assertEqual(renewed['token'],a['token'])
        with self.assertRaises(ValueError):
            run('task_update',{'project':self.p,'id':t,'actor':self.actor,'token':a['token'],'owner_id':human})
        with sqlite3.connect(os.environ['SCRUM_DB']) as db:
            db.execute('UPDATE tasks SET lock_until=0 WHERE id=?',(t,))
        db.close()
        b=run('task_claim',{'project':self.p,'id':t,'actor':human})
        self.assertNotEqual(a['token'],b['token'])
        with self.assertRaises(ValueError):
            run('task_update',{'project':self.p,'id':t,'actor':self.actor,'token':a['token'],'status':'Done'})

    def test_category_and_single_task_lookup(self):
        from mcp_server import handle
        reply=handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'task_create','arguments':{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p,'title':'Polish','category':'Design','actor':self.actor}}})
        result=json.loads(reply['result']['content'][0]['text'])
        t=run('task_get',{'project':self.p,'code':result['code']})
        self.assertEqual(t['category'],'Design')
        self.assertNotIn('activity',t)
        self.assertNotIn('lock_token',t)
        self.update(t['id'],category='Testing')
        self.assertEqual(run('task_get',{'project':self.p,'id':t['id']})['category'],'Testing')
        for invalid in ['', 'x'*81, 4]:
            with self.assertRaises(ValueError):
                self.update(t['id'],category=invalid)
        with self.assertRaises(ValueError):
            run('task_get',{'project':self.q,'code':result['code']})

    def test_multiple_blockers_auto_todo_and_authored_comments(self):
        first,second,third=[self.task(n) for n in ['API','UI','Integration']]
        self.update(third,status='Blocked',dependencies=[first,second])
        self.update(first,status='Done',log='API checks passed.')
        t=run('task_get',{'project':self.p,'id':third})
        self.assertEqual(t['dependencies'],[second])
        self.assertEqual(t['status'],'Blocked')
        result=self.update(second,status='Done')
        self.assertEqual(result['unblocked'][0]['status'],'TODO')
        t=run('task_get',{'project':self.p,'id':third})
        self.assertEqual(t['dependencies'],[])
        self.assertEqual(t['status'],'TODO')
        self.assertFalse(t['locked'])
        history=run('task_activity',{'project':self.p,'id':third})['items']
        comments=[i for i in history if i['kind']=='comment']
        self.assertEqual(len(comments),2)
        self.assertEqual(comments[-1]['actor'],self.actor)
        self.assertTrue(comments[-1]['created'])
        self.assertIn('Test agent',comments[-1]['text'])
        self.assertIn('PRO-2',comments[-1]['text'])
        self.update(second,status='Done')
        self.assertEqual(len([i for i in run('task_activity',{'project':self.p,'id':third})['items'] if i['kind']=='comment']),2)

    def test_resolution_preserves_external_reason_and_project_scope(self):
        first,blocked=[self.task(n) for n in ['Prerequisite','External approval']]
        self.update(blocked,status='Blocked',dependencies=[first],blocked_reason='Await human approval')
        self.update(first,status='Done')
        t=run('task_get',{'project':self.p,'id':blocked})
        self.assertEqual(t['status'],'Blocked')
        self.assertEqual(t['dependencies'],[])
        self.assertEqual(t['blocked_reason'],'Await human approval')
        self.assertIsNone(run('next_work',{'project':self.p})['task'])
        foreign=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.q,'title':'Other project'})['id']
        with self.assertRaises(ValueError):
            self.update(blocked,dependencies=[foreign])
        with self.assertRaises(ValueError):
            run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p,'title':'Invalid','dependencies':[foreign]})
        self.assertEqual(len(run('snapshot',{'project':self.p})['tasks']),2)

    def test_activity_pagination_and_comment_during_another_workers_lock(self):
        t=self.task('Discuss')
        claim=run('task_claim',{'project':self.p,'id':t,'actor':self.actor})
        human=run('user_register',{'identity':'human:reviewer','name':'Reviewer','kind':'human'})['id']
        for i in range(25):
            run('task_activity',{'project':self.p,'id':t,'actor':human,'kind':'comment','text':f'Question {i}'})
        page=run('task_activity',{'project':self.p,'id':t,'limit':10})
        self.assertEqual(len(page['items']),10)
        self.assertTrue(page['has_more'])
        self.assertEqual(page['items'][-1]['text'],'Question 24')
        older=run('task_activity',{'project':self.p,'id':t,'limit':10,'before':page['oldest']})
        self.assertLess(older['latest'],page['oldest'])
        delta=run('task_activity',{'project':self.p,'id':t,'after':page['latest']})
        self.assertEqual(delta['items'],[])
        with self.assertRaises(ValueError):
            run('task_activity',{'project':self.p,'id':t,'actor':human,'text':'Pretend work'})
        run('task_activity',{'project':self.p,'id':t,'actor':self.actor,'token':claim['token'],'text':'Implemented the handler.'})
        self.assertEqual(run('task_activity',{'project':self.p,'id':t,'after':page['latest']})['items'][0]['kind'],'work')
        with self.assertRaises(ValueError):
            run('task_activity',{'project':self.q,'id':t})

    def test_atomic_claim_edit_log_and_failure_rollback(self):
        t,dep=self.task('Claim and edit'),self.task('Dependency')
        with self.assertRaises(ValueError):
            run('task_update',{'project':self.p,'id':t,'actor':self.actor,'claim':True,'category':'Broken','dependencies':[dep]})
        unchanged=run('task_get',{'project':self.p,'id':t})
        self.assertFalse(unchanged['locked'])
        self.assertEqual(unchanged['category'],'General')
        result=run('task_update',{'project':self.p,'id':t,'actor':self.actor,'claim':True,'category':'Backend','log':'Started handler implementation.'})
        self.assertEqual(result['status'],'In Progress')
        self.assertTrue(result['token'])
        run('task_update',{'project':self.p,'id':t,'actor':self.actor,'token':result['token'],'status':'Done','evidence':'Handler behavior test passed.','log':'Handler implemented and verified.'})
        entries=run('task_activity',{'project':self.p,'id':t})['items']
        self.assertEqual([i['text'] for i in entries if i['kind']=='work'], ['Started handler implementation.','Handler implemented and verified.'])

    def test_human_reassign_after_force_release(self):
        t=self.task('Recover')
        human=run('user_register',{'identity':'human:recover','name':'Recover','kind':'human'})['id']
        run('task_claim',{'project':self.p,'id':t,'actor':self.actor})
        run('task_release',{'project':self.p,'id':t,'actor':human,'force':True})
        run('task_update',{'project':self.p,'id':t,'actor':human,'status':'TODO','owner_id':human})
        t=run('task_get',{'project':self.p,'id':t})
        self.assertEqual(t['status'],'TODO')
        self.assertEqual(t['owner_id'],human)

    def test_dependency_cycle_and_no_cross_project_impact(self):
        a,b,c=[self.task(n) for n in ['A','B','C']]
        self.update(b,dependencies=[a,a])
        self.assertEqual(run('task_get',{'project':self.p,'id':b})['dependencies'],[a])
        self.update(c,dependencies=[b])
        with self.assertRaises(ValueError):
            self.update(a,dependencies=[c])
        untouched=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.q,'title':'Other'})['id']
        self.update(a,status='Done')
        self.assertEqual(run('task_get',{'project':self.q,'id':untouched})['status'],'TODO')

    def test_web_handlers_json_boundary_and_new_controls(self):
        from server import Handler
        from types import SimpleNamespace
        class QuietHandler(Handler):
            def log_message(self, *args):
                pass
        class Connection:
            def __init__(self, request):
                self.input=io.BytesIO(request)
                self.output=bytearray()
            def makefile(self,*args):
                return self.input
            def sendall(self,data):
                self.output.extend(data)
        def request(path, body=None, origin=None):
            payload=json.dumps(body).encode() if body is not None else b''
            method='POST' if body is not None else 'GET'
            headers=f'{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:8765\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n'
            if origin:
                headers+=f'Origin: {origin}\r\n'
            connection=Connection(headers.encode()+b'\r\n'+payload)
            QuietHandler(connection,('127.0.0.1',1234),SimpleNamespace(server_port=8765))
            return bytes(connection.output).split(b'\r\n\r\n',1)
        header,html=request('/')
        self.assertIn(b'200',header.splitlines()[0])
        for control in [b'current-user',b'theme-toggle',b'category-filter',b'File locks',b'Settings']:
            self.assertIn(control,html)
        _,js=request('/app.js')
        self.assertIn(b'Updates & comments',js)
        header,body=request('/api/task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p,'title':'Via website','category':'Web','actor':self.actor})
        self.assertIn(b'200',header.splitlines()[0])
        t=json.loads(body)['id']
        self.assertEqual(run('task_get',{'project':self.p,'id':t})['category'],'Web')
        header,_=request('/api/task_update',{'project':self.p,'id':t,'title':'Cross-origin'},'https://example.com')
        self.assertIn(b'403',header.splitlines()[0])
        self.assertEqual(run('task_get',{'project':self.p,'id':t})['title'],'Via website')

if __name__ == '__main__':
    unittest.main()
