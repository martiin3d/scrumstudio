import unittest
import test_storage
from contextlib import closing
from unittest.mock import patch
from store import connect
from store import run

class UsersTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def test_removed_profile_cannot_act_or_be_reassigned_but_authors_survive(self):
        p,path=self.project()
        task=run('task_create',{'project':p,'title':'History','description':'Preserve authored task records.','acceptance_criteria':['Deleted authors remain identifiable.']})['id']
        run('task_activity',{'project':p,'id':task,'actor':self.agent,'kind':'comment','text':'Original author'})
        run('project_chat',{'project':p,'actor':self.agent,'text':'Original message'})
        run('agent_checkpoint',{'project':p,'actor':self.agent,'client':'codex','action':'offline'})
        run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        deleted=next(u for u in run('users',{}) if u['id']==self.agent)
        self.assertEqual(deleted['active'],0)
        with self.assertRaises(ValueError):
            run('task_activity',{'project':p,'id':task,'actor':self.agent,'kind':'comment','text':'Not allowed'})
        with self.assertRaises(ValueError):
            run('task_update',{'project':p,'id':task,'actor':self.human,'owner_id':self.agent})
        with self.assertRaises(ValueError):
            run('user_register',{'identity':'agent:portable','name':'Portable agent','kind':'agent'})
        self.assertEqual(run('task_activity',{'project':p,'id':task})['items'][-1]['actor'],self.agent)
        self.assertEqual(run('project_chat',{'project':p})['items'][0]['author_name'],'Portable agent')
        run('project_export',{'project':p})
        # Same-installation imports must not reactivate deleted historical users.
        import shutil
        clone=self.root/'deleted-author-clone';clone.mkdir();(clone/'.scrum').mkdir()
        for name in ['project.json','snapshot.json']:shutil.copy2(path/'.scrum'/name,clone/'.scrum'/name)
        opened=run('project_open',{'path':str(clone)})
        self.assertEqual(run('project_chat',{'project':opened['id']})['items'][0]['author_name'],'Portable agent')
        self.assertEqual(next(u for u in run('users',{}) if u['id']==self.agent)['active'],0)
        run('user_manage',{'actor':self.human,'id':self.agent,'action':'restore'})
        self.assertEqual(run('user_register',{'identity':'agent:portable','name':'Portable agent','kind':'agent'})['id'],self.agent)

    def test_permissions_last_human_and_cross_project_live_claim_guard(self):
        p,_=self.project()
        with self.assertRaises(ValueError):
            run('user_manage',{'actor':self.agent,'id':self.human,'action':'delete'})
        with self.assertRaises(ValueError):
            run('user_manage',{'actor':self.human,'id':self.human,'action':'delete'})
        task=run('task_create',{'project':p,'title':'Live work','description':'Protect working users.','acceptance_criteria':['Live work prevents deletion.']})['id']
        claim=run('task_claim',{'project':p,'id':task,'actor':self.agent})
        with self.assertRaises(ValueError):
            run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        run('task_release',{'project':p,'id':task,'actor':self.agent,'token':claim['token']})
        with self.assertRaises(ValueError):
            run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        run('agent_checkpoint',{'project':p,'actor':self.agent,'client':'codex','action':'offline'})
        self.assertFalse(run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})['active'])

    def test_recovery_preserves_states_history_and_other_workers_across_projects(self):
        projects=[self.project('One'),self.project('Two')]
        other=run('user_register',{'identity':'agent:other','name':'Other','kind':'agent'})['id']
        expected={}
        for p,path in projects:
            for status in ['TODO','In Progress','Blocked','In Review','Done','Cancelled']:
                task=run('task_create',{'project':p,'title':status,'description':'Recovery fixture','acceptance_criteria':['Preserve content']})['id']
                with closing(connect(path/'.scrum/local/scrum.db',path/'.scrum/local/locks.db')) as db,db:
                    db.execute("UPDATE tasks SET status=?,owner_id=?,lock_token='old-secret',lock_until=9999999999,evidence='review evidence',blocked_reason=? WHERE id=?",(status,self.agent,'external dependency' if status=='Blocked' else '',task))
                    db.execute("INSERT INTO file_locks(path,project,task,owner_id,token_hash,lock_until,created) VALUES(?,?,?,?,?,9999999999,'fixture')",(status,p,task,self.agent,'hash'))
                    db.execute("UPDATE agent_sessions SET status='offline',bridge_heartbeat=0 WHERE actor=?",(self.agent,))
                expected[p,task]=status
            other_task=run('task_create',{'project':p,'title':'Other worker','description':'Keep it','acceptance_criteria':['Unaffected']})['id']
            run('agent_checkpoint',{'project':p,'actor':other,'client':'codex','session_used_percent':10,'weekly_used_percent':20})
            self.assertTrue(run('task_claim',{'project':p,'id':other_task,'actor':other,'files':['other.txt']})['claimed'])
        result=run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        self.assertEqual(result['tasks_recovered'],8)
        self.assertEqual(result['file_reservations_released'],12)
        for (p,task),status in expected.items():
            row=run('task_get',{'project':p,'id':task})
            self.assertEqual(row['status'],'TODO' if status=='In Progress' else status)
            self.assertEqual(row['evidence'],'review evidence')
            self.assertEqual(row['owner_id'],self.agent if status in ('Done','Cancelled') else None)
            self.assertFalse(row['locked'])
            if status not in ('Done','Cancelled'):
                self.assertEqual(run('task_activity',{'project':p,'id':task})['items'][-1]['actor'],self.human)
        for p,path in projects:
            with closing(connect(path/'.scrum/local/scrum.db',path/'.scrum/local/locks.db')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM file_locks WHERE owner_id=?',(other,)).fetchone()[0],1)
        self.assertEqual(run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})['tasks_recovered'],0)
        with self.assertRaises(ValueError):
            run('task_claim',{'project':projects[0][0],'id':next(iter(expected))[1],'actor':self.agent,'token':'old-secret'})

    def test_missing_folder_fails_before_recovery(self):
        p,path=self.project('First');second,missing=self.project('Missing')
        task=run('task_create',{'project':p,'title':'Unstarted','description':'Keep on failure','acceptance_criteria':['No premature recovery']})['id']
        run('task_update',{'project':p,'id':task,'actor':self.human,'owner_id':self.agent})
        missing.rename(missing.with_name('temporarily-moved'))
        with self.assertRaisesRegex(ValueError,'cannot recover project'):
            run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        self.assertEqual(run('task_get',{'project':p,'id':task})['owner_id'],self.agent)
        self.assertEqual(next(u for u in run('users',{}) if u['id']==self.agent)['active'],1)

    def test_managed_stop_requires_exit_and_retry(self):
        p,path=self.project()
        import managed_agents
        key='a'*32
        with closing(managed_agents.connect(path)) as db,db:
            db.execute("INSERT INTO profiles(id,name,client,instructions,source,usage_file,actor,desired,resume_on_wakeup) VALUES(?, 'fixture','codex','','','',?,1,1)",(key,self.agent))
        result=run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        self.assertTrue(result['pending'])
        with closing(managed_agents.connect(path)) as db:
            self.assertEqual(tuple(db.execute('SELECT desired,resume_on_wakeup FROM profiles').fetchone()),(0,0))
        run('agent_checkpoint',{'project':p,'actor':self.agent,'client':'codex','action':'offline'})
        self.assertFalse(run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})['active'])
        with self.assertRaisesRegex(ValueError,'deleted'):
            run('agent_manage',{'project':p,'actor':self.human,'action':'start','id':key})

    def test_partial_failure_disables_identity_and_retry_does_not_duplicate_activity(self):
        import storage
        projects=[self.project('First'),self.project('Second')]
        tasks=[]
        for p,path in projects:
            task=run('task_create',{'project':p,'title':'Recover','description':'Retry safely','acceptance_criteria':['No duplicated recovery']})['id']
            run('task_update',{'project':p,'id':task,'actor':self.human,'owner_id':self.agent})
            run('agent_checkpoint',{'project':p,'actor':self.agent,'client':'codex','action':'offline'})
            tasks.append(task)
        original=storage.recover_agent_tasks
        calls=[]
        def fail_second(*args):
            calls.append(1)
            if len(calls)==2:raise ValueError('Injected unavailable storage')
            return original(*args)
        with patch('storage.recover_agent_tasks',side_effect=fail_second):
            with self.assertRaisesRegex(ValueError,'profile is disabled; recovery incomplete'):
                run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})
        self.assertEqual(next(u for u in run('users',{}) if u['id']==self.agent)['active'],0)
        first={'project':projects[0][0],'id':tasks[0]}
        before=run('task_activity',first)['items']
        self.assertEqual(run('user_manage',{'actor':self.human,'id':self.agent,'action':'delete'})['tasks_recovered'],1)
        self.assertEqual(run('task_activity',first)['items'],before)
