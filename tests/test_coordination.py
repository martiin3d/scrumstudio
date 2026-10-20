import json
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from store import run


class CoordinationTest(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data'
        root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.previous=os.environ.get('SCRUM_DB')
        os.environ['SCRUM_DB']=str(Path(self.temp.name)/'coord.db')
        self.root=str(Path(self.temp.name).resolve())
        self.p=run('project_create',{'name':'Product'})['id']
        self.q=run('project_create',{'name':'Other'})['id']
        self.human=run('user_register',{'identity':'human:test','name':'Human','kind':'human'})['id']
        self.a=run('user_register',{'identity':'agent:a','name':'Agent A','kind':'agent'})['id']
        self.b=run('user_register',{'identity':'agent:b','name':'Agent B','kind':'agent'})['id']
        for project in [self.p,self.q]:
            run('project_settings',{'project':project,'actor':self.human,'workspace_root':self.root,'max_parallel_agents':20})

    def tearDown(self):
        if self.previous is None:
            os.environ.pop('SCRUM_DB',None)
        else:
            os.environ['SCRUM_DB']=self.previous
        self.temp.cleanup()

    def task(self, files=None, project=None):
        return run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':project or self.p,'title':'Work','files':files or []})['id']

    def claim(self, task, actor=None, project=None, **kw):
        return run('task_claim',dict(project=project or self.p,id=task,actor=actor or self.a,**kw))

    def test_policy_permissions_limits_and_renewal(self):
        s=run('project_settings',{'project':self.p})
        self.assertEqual(s['max_active_per_agent'],1)
        self.assertTrue(s['require_evidence'])
        for args in [{'actor':self.a,'max_active_per_agent':2},{'actor':self.human,'max_active_per_agent':0},{'actor':self.human,'max_active_per_agent':21}]:
            with self.assertRaises(ValueError):
                run('project_settings',dict(project=self.p,**args))
        first,second=self.task(),self.task()
        a=self.claim(first)
        self.claim(first,token=a['token'])
        with self.assertRaises(ValueError):
            self.claim(second)
        with self.assertRaises(ValueError):
            run('task_update',{'project':self.p,'id':second,'actor':self.a,'claim':True})
        run('project_settings',{'project':self.p,'actor':self.human,'max_active_per_agent':2})
        self.claim(second)
        with self.assertRaises(ValueError):
            run('project_settings',{'project':self.p,'actor':self.human,'max_active_per_agent':1})
        third=self.task(project=self.q)
        self.assertTrue(self.claim(third,project=self.q)['claimed'])

    def test_concurrent_wip_claims_one_winner(self):
        tasks=[self.task() for _ in range(4)]
        def claim(task):
            try:
                return self.claim(task)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims=list(pool.map(claim,tasks))
        self.assertEqual(len([c for c in claims if c]),1)
        context=run('next_work',{'project':self.p,'actor':self.a})
        self.assertEqual(context['capacity'],{'active':1,'limit':1})
        self.assertEqual(context['task']['owner_id'],self.a)

    def test_human_exempt_and_expired_claim_not_capacity(self):
        first,second=self.task(),self.task()
        self.claim(first,actor=self.human)
        self.claim(second,actor=self.human)
        t=self.task()
        self.claim(t)
        with closing(sqlite3.connect(os.environ['SCRUM_DB'])) as db,db:
            db.execute('UPDATE tasks SET lock_until=0 WHERE id=?',(t,))
        self.assertTrue(self.claim(self.task())['claimed'])

    def test_done_evidence_and_checklist_required_and_reopen(self):
        t=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.p,'title':'Check','checklist':[{'text':'Test','done':False}]})['id']
        a=self.claim(t)
        base={'project':self.p,'id':t,'actor':self.a,'token':a['token'],'status':'Done'}
        for fields in [{},{'evidence':'Test passed'},{'evidence':'  ','checklist':[{'text':'Test','done':True}]}]:
            with self.assertRaises(ValueError):
                run('task_update',dict(base,**fields))
        run('task_update',dict(base,evidence='Behavior test passed; expected output observed.',checklist=[{'text':'Test','done':True}]))
        self.assertEqual(run('task_get',{'project':self.p,'id':t})['status'],'Done')
        run('task_update',{'project':self.p,'id':t,'status':'TODO','actor':self.human})
        self.assertEqual(run('task_get',{'project':self.p,'id':t})['evidence'],'')
        with self.assertRaises(ValueError):
            run('task_update',{'project':self.p,'id':t,'status':'Done','actor':self.a})
        run('project_settings',{'project':self.p,'actor':self.human,'require_evidence':False})
        run('task_update',{'project':self.p,'id':t,'status':'Done','actor':self.a})

    def test_global_file_conflict_atomic_claim_and_next_candidate(self):
        first=self.task(['src/shared.py'])
        a=self.claim(first)
        second=self.task(['./src/shared.py','src/new.py'],project=self.q)
        result=self.claim(second,actor=self.b,project=self.q)
        self.assertFalse(result['claimed'])
        self.assertEqual(result['conflicts'][0]['project'],self.p)
        self.assertEqual(run('task_get',{'project':self.q,'id':second})['status'],'TODO')
        self.assertEqual(run('file_locks',{'project':self.q})['items'],[])
        free=self.task(['src/other.py'],project=self.q)
        work=run('next_work',{'project':self.q,'actor':self.b})
        self.assertEqual(work['task']['id'],free)
        self.assertEqual(work['file_conflicts_skipped'],1)
        self.assertEqual(len(run('file_locks',{'project':self.p})['items']),1)
        run('task_update',{'project':self.p,'id':first,'actor':self.a,'token':a['token'],'status':'Done','evidence':'Verified source output.'})
        self.assertEqual(run('file_locks',{'project':self.p})['items'],[])
        self.assertTrue(self.claim(second,actor=self.b,project=self.q)['claimed'])

    def test_atomic_claim_update_file_conflict_rolls_back_fields(self):
        first=self.task(['shared.py'])
        self.claim(first)
        second=self.task()
        r=run('task_update',{'project':self.p,'id':second,'actor':self.b,'claim':True,'category':'Changed','files':['shared.py','free.py']})
        self.assertFalse(r['claimed'])
        t=run('task_get',{'project':self.p,'id':second})
        self.assertEqual(t['category'],'General')
        self.assertFalse(t['locked'])
        self.assertEqual(t['files'],[])

    def test_batch_acquire_no_partial_and_token_check(self):
        self.claim(self.task(['busy.py']))
        t=self.task()
        b=self.claim(t,actor=self.b)
        args={'project':self.p,'action':'acquire','id':t,'actor':self.b,'token':b['token']}
        r=run('file_locks',dict(args,paths=['free.py','busy.py']))
        self.assertFalse(r['acquired'])
        self.assertEqual(run('task_get',{'project':self.p,'id':t})['files'],[])
        self.assertFalse(any(l['path'].endswith('free.py') for l in run('file_locks',{'project':self.p})['items']))
        with self.assertRaises(ValueError):
            run('file_locks',dict(args,token='wrong',paths=['free.py']))
        self.assertEqual(run('file_locks',dict(args,paths=['free.py']))['plan_added'],1)
        self.assertEqual(run('task_get',{'project':self.p,'id':t})['files'],['free.py'])
        self.assertEqual(run('file_locks',dict(args,paths=['free.py']))['plan_added'],0)

    def test_path_normalization_escape_and_workspace_policy(self):
        t=self.task(['src/a.py'])
        claim=self.claim(t)
        locks=run('file_locks',{'project':self.p})['items']
        self.assertEqual(locks[0]['path'],os.path.normcase(str(Path(self.root)/'src'/'a.py')))
        other=self.task(['src/../src/a.py'])
        self.assertFalse(self.claim(other,actor=self.b)['claimed'])
        with self.assertRaises(ValueError):
            run('project_settings',{'project':self.p,'actor':self.human,'workspace_root':str(Path(self.root)/'new')})
        for invalid in ['../outside.py',self.root]:
            with self.assertRaises(ValueError):
                run('file_locks',{'project':self.p,'action':'acquire','id':t,'actor':self.a,'token':claim['token'],'paths':[invalid]})
        p=run('project_create',{'name':'Unset'})['id']
        pending=self.task(['x.py'],project=p)
        with self.assertRaises(ValueError):
            self.claim(pending,project=p)
        self.assertEqual(run('next_work',{'project':p,'actor':self.a})['missing_workspace_skipped'],1)

    def test_resume_cleanup_own_release_all_and_human_scope(self):
        a=self.claim(self.task(['a.py','b.py']))
        self.claim(self.task(['other.py']),actor=self.b)
        remote=self.task(['remote.py'],project=self.q)
        self.claim(remote,project=self.q)
        resume=run('session_resume',{'project':self.p,'actor':self.a,'limit':1})
        self.assertTrue(resume['own_file_locks']['has_more'])
        self.assertEqual(resume['settings']['max_active_per_agent'],1)
        locks=run('file_locks',{'project':self.p,'actor':self.a,'own':True})['items']
        run('file_locks',{'project':self.p,'action':'release','actor':self.a,'ids':[locks[0]['id']]})
        self.assertEqual(run('file_locks',{'project':self.p,'action':'release','actor':self.a,'all':True})['released'],1)
        self.assertEqual(len(run('file_locks',{'project':self.p})['items']),1)
        with self.assertRaises(ValueError):
            run('file_locks',{'project':self.p,'action':'release','actor':self.a,'all':True,'force':True})
        self.assertEqual(run('file_locks',{'project':self.p,'action':'release','actor':self.human,'all':True,'force':True})['released'],1)
        self.assertEqual(len(run('file_locks',{'project':self.q})['items']),1)

    def test_task_release_cleans_files_and_renewals_follow_task(self):
        t=self.task(['a.py'])
        a=self.claim(t)
        with closing(sqlite3.connect(os.environ['SCRUM_DB'])) as db,db:
            db.execute('UPDATE file_locks SET lock_until=? WHERE task=?',(time.time()+60,t))
        run('task_activity',{'project':self.p,'id':t,'actor':self.a,'token':a['token'],'text':'Milestone complete.'})
        until=run('file_locks',{'project':self.p})['items'][0]['lock_until']
        self.assertGreater(until,time.time()+3000)
        run('task_release',{'project':self.p,'id':t,'actor':self.a,'token':a['token']})
        self.assertEqual(run('file_locks',{'project':self.p})['items'],[])

    def test_expired_generation_and_settings_cache(self):
        t=self.task(['a.py'])
        a=self.claim(t)
        with closing(sqlite3.connect(os.environ['SCRUM_DB'])) as db,db:
            db.execute('UPDATE tasks SET lock_until=0 WHERE id=?',(t,))
            db.execute('UPDATE file_locks SET lock_until=0 WHERE task=?',(t,))
        b=self.claim(t,actor=self.b)
        self.assertNotEqual(a['token'],b['token'])
        with self.assertRaises(ValueError):
            run('file_locks',{'project':self.p,'action':'acquire','id':t,'actor':self.a,'token':a['token'],'paths':['a.py']})
        context=run('next_work',{'project':self.p,'actor':self.b})
        version=context['settings']['settings_version']
        self.assertIsNone(run('next_work',{'project':self.p,'actor':self.b,'settings_seen':version})['settings'])
        serialized=json.dumps(run('file_locks',{'project':self.p}))
        self.assertNotIn(b['token'],serialized)
        self.assertNotIn('token_hash',serialized)

    def test_mcp_policy_resume_and_file_lock_flow(self):
        from mcp_server import handle
        def call(name,args):
            r=handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})['result']
            self.assertNotIn('isError',r,r)
            return json.loads(r['content'][0]['text'])
        self.assertTrue(call('project_settings',{'project':self.p})['require_evidence'])
        self.assertEqual(call('session_resume',{'project':self.p,'actor':self.a})['own_file_locks']['items'],[])
        t=self.task(['mcp.py'])
        claim=call('task_claim',{'project':self.p,'id':t,'actor':self.a})
        self.assertEqual(claim['files_reserved'],1)
        self.assertEqual(call('file_locks',{'project':self.p})['items'][0]['task'],t)

    def test_temporary_exclusions_and_policy_cache_invalidation(self):
        first,second=self.task(),self.task()
        work=run('next_work',{'project':self.p,'actor':self.a})
        version=work['settings']['settings_version']
        self.assertEqual(run('next_work',{'project':self.p,'actor':self.a,'exclude_ids':[first]})['task']['id'],second)
        self.assertEqual(run('next_work',{'project':self.p,'actor':self.a})['task']['id'],first)
        run('project_settings',{'project':self.p,'actor':self.human,'max_active_per_agent':3})
        changed=run('next_work',{'project':self.p,'actor':self.a,'settings_seen':version})
        self.assertEqual(changed['settings']['max_active_per_agent'],3)
        self.assertGreater(changed['settings']['settings_version'],version)

    def test_concurrent_cross_project_file_claim_one_winner(self):
        first=self.task(['same.py'])
        second=self.task(['./same.py'],project=self.q)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda v:self.claim(v[0],actor=v[1],project=v[2]),[(first,self.a,self.p),(second,self.b,self.q)]))
        self.assertEqual(sum(r['claimed'] for r in results),1)
        self.assertEqual(len(run('file_locks',{'project':self.p})['items'])+len(run('file_locks',{'project':self.q})['items']),1)

    def test_real_legacy_schema_migrates_without_losing_task(self):
        previous=os.environ['SCRUM_DB']
        legacy=str(Path(self.temp.name)/'legacy.db')
        with closing(sqlite3.connect(legacy)) as db,db:
            db.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '')")
            db.execute("INSERT INTO projects(name) VALUES('Legacy')")
            db.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY,project INTEGER NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'TODO',priority INTEGER NOT NULL DEFAULT 2,blocked_reason TEXT NOT NULL DEFAULT '',dependencies TEXT NOT NULL DEFAULT '[]',updated TEXT NOT NULL)")
            db.execute("INSERT INTO tasks(project,title,updated) VALUES(1,'Keep this','2026-10-17')")
        os.environ['SCRUM_DB']=legacy
        try:
            task=run('task_get',{'project':1,'id':1})
            self.assertEqual(task['title'],'Keep this')
            self.assertEqual(task['code'],'LEG-1')
            self.assertEqual(task['files'],[])
            self.assertEqual(task['evidence'],'')
            self.assertEqual(run('project_settings',{'project':1})['max_active_per_agent'],1)
        finally:
            os.environ['SCRUM_DB']=previous
