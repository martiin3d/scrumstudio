from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from store import run, connect
import storage

ROOT = Path(__file__).resolve().parents[1]

class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'data')
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'SCRUM_REGISTRY':str(self.root/'registry.db'), 'SCRUM_DB':str(self.root/'legacy.db')})
        self.env.start()
        self.agent = run('user_register',{'identity':'agent:portable','name':'Portable agent','kind':'agent'})['id']
        self.human = run('user_register',{'identity':'human:portable','name':'Human','kind':'human'})['id']

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def project(self, name='Project'):
        path = self.root / (name+str(len(list(self.root.iterdir()))))
        path.mkdir()
        p = run('project_create',{'path':str(path),'name':name})
        # Non-capacity fixtures explicitly permit independent parallel scenarios.
        run('project_settings',{'project':p['id'],'actor':self.human,'max_parallel_agents':20})
        run('agent_checkpoint',{'project':p['id'],'actor':self.agent,'client':'codex','session_used_percent':10,'weekly_used_percent':20})
        return p['id'],path

    def clone(self, path):
        clone = self.root / ('clone'+str(len(list(self.root.iterdir()))))
        clone.mkdir()
        (clone/'.scrum').mkdir()
        for name in ['project.json','snapshot.json']:
            shutil.copy2(path/'.scrum'/name,clone/'.scrum'/name)
        opened=run('project_open',{'path':str(clone)})
        # Imported identities may have different numeric IDs on another machine.
        actor=next((u['id'] for u in run('users',{}) if u['identity']=='agent:portable'),None)
        if actor:
            run('agent_checkpoint',{'project':opened['id'],'actor':actor,'client':'codex','session_used_percent':10,'weekly_used_percent':20})
        return opened,clone

    def test_layout_registry_and_checkout_isolation(self):
        p,path = self.project()
        for name in ['project.json','snapshot.json','local/scrum.db','local/locks.db','local/checkout.json','.gitignore']:
            self.assertTrue((path/'.scrum'/name).exists(),name)
        self.assertEqual(run('project_settings',{'project':p})['workspace_root'],str(path))
        self.assertEqual(run('project_open',{'path':str(path)})['id'],p)
        second,clone = self.clone(path)
        projects=run('projects',{})
        self.assertEqual(projects[0]['uuid'],second['uuid'])
        self.assertNotEqual(projects[0]['checkout'],second['checkout'])
        self.assertEqual(run('project_settings',{'project':second['id']})['workspace_root'],str(clone))
        with closing(sqlite3.connect(self.root/'registry.db')) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}, {'folders','sqlite_sequence'})
        with closing(sqlite3.connect(path/'.scrum/local/scrum.db')) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='file_locks'").fetchone())

    def test_concurrent_project_creation_and_open(self):
        folders=[self.root/('parallel'+str(i)) for i in range(4)]
        for folder in folders:
            folder.mkdir()
        with ThreadPoolExecutor(max_workers=4) as pool:
            created=list(pool.map(lambda f:run('project_create',{'name':f.name,'path':str(f)}),folders))
        self.assertEqual(len({p['id'] for p in created}),4)
        with ThreadPoolExecutor(max_workers=4) as pool:
            opened=list(pool.map(lambda _:run('project_open',{'path':str(folders[0])}),range(4)))
        self.assertEqual({p['id'] for p in opened},{created[0]['id']})

    def test_overlapping_project_paths_do_not_share_locks(self):
        parent,folder=self.project()
        nested=folder/'nested';nested.mkdir()
        child=run('project_create',{'path':str(nested),'name':'Nested'})['id']
        run('agent_checkpoint',{'project':child,'actor':self.agent,'client':'codex','session_used_percent':10,'weekly_used_percent':20})
        first=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':parent,'title':'Parent','files':['nested/shared.py']})['id']
        second=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':child,'title':'Child','files':['shared.py']})['id']
        self.assertTrue(run('task_claim',{'project':parent,'id':first,'actor':self.agent})['claimed'])
        self.assertTrue(run('task_claim',{'project':child,'id':second,'actor':self.agent})['claimed'])
        self.assertEqual(run('file_locks',{'project':parent})['items'][0]['path'],run('file_locks',{'project':child})['items'][0]['path'])
        run('file_locks',{'project':child,'action':'release','actor':self.agent,'all':True})
        self.assertEqual(len(run('file_locks',{'project':parent})['items']),1)

    def test_import_remaps_machine_actor_ids_and_preserves_bytes(self):
        p,path=self.project()
        task=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Tracked','actor':self.agent})['id']
        run('task_update',{'project':p,'id':task,'status':'Done','actor':self.agent,'evidence':'Verified'})
        run('project_export',{'project':p})
        original=(path/'.scrum/snapshot.json').read_bytes()
        with patch.dict(os.environ,{'SCRUM_REGISTRY':str(self.root/'other-machine'/'registry.db')}):
            run('user_register',{'identity':'human:other','name':'Other','kind':'human'})
            opened,clone=self.clone(path)
            imported=run('task_get',{'project':opened['id'],'code':'PRO-1'})
            activity=run('task_activity',{'project':opened['id'],'id':imported['id']})['items']
            actor=next(u for u in run('users',{}) if u['identity']=='agent:portable')
            self.assertNotEqual(actor['id'],self.agent)
            self.assertEqual(activity[-1]['actor'],actor['id'])
            run('project_export',{'project':opened['id']})
            self.assertEqual(original,(clone/'.scrum/snapshot.json').read_bytes())

    def test_missing_folder_can_be_forgotten(self):
        p,path=self.project()
        moved=path.with_name('relocated');path.rename(moved)
        self.assertFalse(run('projects',{})[0]['available'])
        run('project_forget',{'project':p})
        reopened=run('project_open',{'path':str(moved)})
        self.assertEqual(run('project_settings',{'project':reopened['id']})['workspace_root'],str(moved))

    def test_full_roundtrip_and_no_secret_locks(self):
        p,path=self.project()
        first=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'API','files':[str(path/'src/api.py')],'actor':self.agent})['id']
        second=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Client','dependencies':[first]})['id']
        claim=run('task_claim',{'project':p,'id':first,'actor':self.agent})
        run('task_activity',{'project':p,'id':first,'actor':self.agent,'token':claim['token'],'text':'Built contract'})
        run('project_chat',{'project':p,'actor':self.agent,'recipient':self.human,'task':first,'text':'Review contract'})
        run('note_add',{'project':p,'kind':'Review','title':'Check','items':['API contract']})
        run('project_export',{'project':p})
        snapshot=path/'.scrum/snapshot.json'
        exported=snapshot.read_bytes()
        self.assertNotIn(claim['token'].encode(),exported)
        for secret in [b'lock_token',b'lock_until',b'token_hash',str(path).encode()]:
            self.assertNotIn(secret,exported)
        run('project_export',{'project':p})
        self.assertEqual(exported,snapshot.read_bytes())
        opened,clone=self.clone(path)
        q=opened['id']
        tasks=run('snapshot',{'project':q})['tasks']
        api=next(t for t in tasks if t['title']=='API')
        client=next(t for t in tasks if t['title']=='Client')
        self.assertEqual(client['dependencies'],[api['id']])
        self.assertFalse(api['locked'])
        self.assertEqual(api['files'],['src/api.py'])
        self.assertEqual(run('file_locks',{'project':q})['items'],[])
        self.assertTrue(run('task_claim',{'project':q,'id':api['id'],'actor':self.agent})['claimed'])
        self.assertEqual(run('project_chat',{'project':q})['items'][0]['text'],'Review contract')
        self.assertEqual(len(run('file_locks',{'project':p})['items']),1)
        self.assertEqual(run('session_resume',{'project':q,'actor':self.agent})['own_file_locks']['items'][0]['project'],q)

    def test_forget_reopen_and_restore(self):
        p,path=self.project()
        task=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Saved'})['id']
        run('project_export',{'project':p})
        run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Local only'})
        run('project_open',{'path':str(path)})
        self.assertEqual(len(run('snapshot',{'project':p})['tasks']),2)
        with self.assertRaises(ValueError):
            run('project_restore',{'project':p})
        claim=run('task_claim',{'project':p,'id':task,'actor':self.agent})
        with self.assertRaises(ValueError):
            run('project_restore',{'project':p,'confirm':True})
        with self.assertRaises(ValueError):
            run('project_forget',{'project':p})
        run('task_release',{'project':p,'id':task,'actor':self.agent,'token':claim['token']})
        restored=run('project_restore',{'project':p,'confirm':True})
        self.assertTrue(Path(restored['backup']).exists())
        self.assertEqual(len(run('snapshot',{'project':p})['tasks']),1)
        run('project_forget',{'project':p})
        self.assertEqual(run('projects',{}),[])
        reopened=run('project_open',{'path':str(path)})
        self.assertNotEqual(reopened['id'],p)
        self.assertEqual(run('snapshot',{'project':reopened['id']})['tasks'][0]['title'],'Saved')

    def test_invalid_snapshot_preserves_local_work(self):
        p,path=self.project()
        run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Keep'})
        run('project_export',{'project':p})
        file=path/'.scrum/snapshot.json'
        good=json.loads(file.read_text())
        for alter in [lambda d:d.update(project_uuid=str(__import__('uuid').uuid4())),lambda d:d['tables']['tasks'][0].update(files=['../escape']),lambda d:d['tables']['tasks'][0].update(lock_token='forged'),lambda d:d['tables']['tasks'][0].update(dependencies=[d['tables']['tasks'][0]['uid']])]:
            data=json.loads(json.dumps(good));alter(data);file.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                run('project_restore',{'project':p,'confirm':True})
            self.assertEqual(run('snapshot',{'project':p})['tasks'][0]['title'],'Keep')

    def test_legacy_migration_preserves_ids_history_and_live_claim(self):
        legacy=self.root/'legacy.db'
        from store import legacy_run
        p=legacy_run('project_create',{'name':'Legacy'})['id']
        user=legacy_run('user_register',{'identity':'agent:portable','name':'Portable agent','kind':'agent'})['id']
        task=legacy_run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Existing','actor':user})['id']
        claim=legacy_run('task_claim',{'project':p,'id':task,'actor':user})
        destination=self.root/'migrated';destination.mkdir()
        migrated=run('project_migrate',{'project':p,'path':str(destination)})
        self.assertTrue(Path(migrated['backup']).exists())
        self.assertEqual(migrated['id'],p)
        run('task_update',{'project':p,'id':task,'actor':self.agent,'token':claim['token'],'status':'Done','evidence':'Preserved token validated'})
        self.assertEqual(run('task_get',{'project':p,'id':task})['status'],'Done')
        self.assertGreater(len(run('task_activity',{'project':p,'id':task})['items']),1)
        self.assertTrue(legacy.exists())
        run('project_forget',{'project':p})
        self.assertEqual(run('projects',{}),[])

    def test_failed_migration_does_not_publish_partial_project(self):
        from store import legacy_run
        p=legacy_run('project_create',{'name':'Legacy'})['id']
        task=legacy_run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':p,'title':'Invalid plan','files':[str(self.root/'outside.py')]})['id']
        destination=self.root/'destination';destination.mkdir()
        with self.assertRaises(ValueError):
            run('project_migrate',{'project':p,'path':str(destination)})
        self.assertFalse((destination/'.scrum').exists())
        self.assertEqual(legacy_run('task_get',{'project':p,'id':task})['title'],'Invalid plan')
