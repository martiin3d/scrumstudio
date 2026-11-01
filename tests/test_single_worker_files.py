import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import unittest
import test_storage
from store import run
from coordination_api import execute

class SingleWorkerFilesTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    def setup_project(self,parallel=1,policy='Auto'):
        p,root=self.project()
        run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=parallel,max_active_per_agent=3,file_lock_policy=policy))
        return p,root
    def task(self,p,title='Outcome',files=None):
        return run('task_create',dict(project=p,title=title,description='Verify a concrete coordination outcome',acceptance_criteria=['Behavior verified'],files=files or ['a.py']))['id']
    def claim(self,p,t,actor=None):return run('task_claim',dict(project=p,id=t,actor=actor or self.agent))
    def finish(self,p,t,claim,**args):
        return execute('work_finish',dict(project=p,id=t,actor=self.agent,token=claim['token'],evidence='Targeted behavior passed.',**args))
    def test_single_worker_has_task_claim_but_no_file_leases(self):
        p,_=self.setup_project();t=self.task(p);claim=self.claim(p,t)
        self.assertTrue(claim['claimed']);self.assertEqual(claim['file_lock_mode'],'checkout');self.assertEqual(claim['files_reserved'],0)
        self.assertTrue(run('task_get',dict(project=p,id=t))['locked'])
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])
        old=run('file_locks',dict(project=p,id=t,actor=self.agent,token=claim['token'],action='acquire',paths=['new.py']))
        self.assertTrue(old['acquired']);self.assertEqual(old['count'],0);self.assertEqual(old['plan_added'],1)
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])
    def test_second_claim_is_refused_for_same_actor_peer_and_human(self):
        p,_=self.setup_project();a=self.task(p,'First');b=self.task(p,'Second',files=['b.py']);self.claim(p,a)
        peer=run('user_register',dict(identity='agent:single-peer',name='Peer',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=peer,client='codex',session_used_percent=10,weekly_used_percent=20))
        for actor in (self.agent,peer,self.human):
            with self.assertRaisesRegex(ValueError,'Single-worker checkout'):self.claim(p,b,actor)
        self.assertEqual(run('task_get',dict(project=p,id=b))['status'],'TODO')
    def test_concurrent_mode_still_reserves_and_rejects_overlapping_files(self):
        p,_=self.setup_project(parallel=2);a=self.task(p,'First');b=self.task(p,'Second');c=self.task(p,'Independent',files=['c.py'])
        claim=self.claim(p,a);self.assertEqual(claim['file_lock_mode'],'files');self.assertEqual(claim['files_reserved'],1)
        self.assertFalse(self.claim(p,b)['claimed']);self.assertTrue(self.claim(p,c)['claimed'])
        self.assertEqual(len(run('file_locks',dict(project=p))['items']),2)
    def test_always_policy_preserves_legacy_single_worker_reservations(self):
        p,_=self.setup_project(policy='Always');t=self.task(p);claim=self.claim(p,t)
        self.assertEqual(claim['file_lock_mode'],'files');self.assertEqual(len(run('file_locks',dict(project=p))['items']),1)
    def test_finish_extends_only_explicit_extra_files_without_lock_call(self):
        p,root=self.setup_project();t=self.task(p);claim=self.claim(p,t)
        (root/'b.py').write_text('New output');(root/'unrelated.txt').write_text('Preserve unrelated work')
        r=self.finish(p,t,claim,additional_files=['b.py'])
        self.assertEqual(r['completed']['status'],'Done')
        task=run('task_get',dict(project=p,id=t));self.assertEqual(task['files'],['a.py','b.py'])
        self.assertEqual((root/'unrelated.txt').read_text(),'Preserve unrelated work');self.assertEqual(run('file_locks',dict(project=p))['items'],[])
    def test_invalid_extra_scope_rolls_back_completion_and_file_plan(self):
        p,_=self.setup_project();t=self.task(p);claim=self.claim(p,t)
        with self.assertRaisesRegex(ValueError,'escapes'):self.finish(p,t,claim,additional_files=['../foreign.py'])
        task=run('task_get',dict(project=p,id=t));self.assertEqual(task['status'],'In Progress');self.assertEqual(task['files'],['a.py']);self.assertEqual(task['evidence'],'')
    def test_concurrent_finish_cannot_add_unreserved_files_after_editing(self):
        p,_=self.setup_project(parallel=2);t=self.task(p);claim=self.claim(p,t)
        with self.assertRaisesRegex(ValueError,'exclusive single-worker'):self.finish(p,t,claim,additional_files=['b.py'])
        self.assertEqual(run('task_get',dict(project=p,id=t))['status'],'In Progress')
    def test_live_exclusive_claim_prevents_unsafe_policy_transition(self):
        p,_=self.setup_project();t=self.task(p);claim=self.claim(p,t)
        for changes in ({'max_parallel_agents':2},{'file_lock_policy':'Always'}):
            with self.assertRaisesRegex(ValueError,'Finish live single-worker'):run('project_settings',dict(project=p,actor=self.human,**changes))
        self.finish(p,t,claim)
        self.assertEqual(run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=2))['max_parallel_agents'],2)
    def test_policy_is_human_only_and_rejects_unknown_modes(self):
        p,_=self.setup_project()
        with self.assertRaisesRegex(ValueError,'Only a human'):run('project_settings',dict(project=p,actor=self.agent,file_lock_policy='Always'))
        with self.assertRaises(ValueError):run('project_settings',dict(project=p,actor=self.human,file_lock_policy='Off'))
    def test_stop_and_quota_prevent_late_scope_expansion(self):
        p,_=self.setup_project();t=self.task(p);claim=self.claim(p,t)
        run('project_control',dict(project=p,actor=self.human,action='stop'))
        with self.assertRaises(ValueError):self.finish(p,t,claim,additional_files=['b.py'])
        self.assertEqual(run('task_get',dict(project=p,id=t))['files'],['a.py'])
    def test_simultaneous_claims_have_exactly_one_winner(self):
        p,_=self.setup_project();a=self.task(p,'First');b=self.task(p,'Second',files=['b.py'])
        peer=run('user_register',dict(identity='agent:race-peer',name='Peer',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=peer,client='codex',session_used_percent=10,weekly_used_percent=20))
        def attempt(args):
            try:return bool(self.claim(p,*args).get('claimed'))
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[(a,self.agent),(b,peer)]))
        self.assertEqual(sum(results),1)
    def test_legacy_live_claims_keep_protection_until_they_finish(self):
        p,_=self.setup_project(policy='Always');a=self.task(p,'First');b=self.task(p,'Second',files=['b.py'])
        ca=self.claim(p,a);cb=self.claim(p,b)
        run('project_settings',dict(project=p,actor=self.human,file_lock_policy='Auto'))
        renewed=run('task_claim',dict(project=p,id=a,actor=self.agent,token=ca['token']))
        self.assertEqual(renewed['file_lock_mode'],'files');self.assertEqual(len(run('file_locks',dict(project=p))['items']),2)
        self.finish(p,b,cb);self.finish(p,a,ca)
        c=self.task(p,'Next');self.assertEqual(self.claim(p,c)['file_lock_mode'],'checkout')
    def test_git_commit_includes_declared_extra_and_preserves_unrelated_edit(self):
        p,root=self.setup_project()
        def g(*args):
            r=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8');self.assertEqual(r.returncode,0,r.stderr);return r.stdout.strip()
        g('init','-b','main');g('config','user.name','Fixture');g('config','user.email','fixture@example.test')
        (root/'.gitignore').write_text('.scrum/\n')
        for name in ('a.py','b.py','unrelated.py'):(root/name).write_text('old\n')
        g('add','.gitignore','a.py','b.py','unrelated.py');g('commit','-m','Initial')
        run('project_settings',dict(project=p,actor=self.human,git_mode='Branches',git_push='Never'))
        t=self.task(p);claim=self.claim(p,t)
        for name in ('a.py','b.py','unrelated.py'):(root/name).write_text('changed\n')
        result=self.finish(p,t,claim,additional_files=['b.py'])
        self.assertEqual(set(g('show','--pretty=format:','--name-only','HEAD').splitlines()),{'a.py','b.py'},json.dumps(result))
        self.assertEqual(g('diff','--name-only'), 'unrelated.py')

    def test_batch_finishes_then_claims_successor_without_file_reservations(self):
        p,_=self.setup_project()
        fields=dict(scope_size='Small',scope_reason='Shared bounded context',context_group='same-component')
        a=self.task(p,'First');b=self.task(p,'Second',files=['b.py'])
        for task in (a,b):run('task_update',dict(project=p,id=task,actor=self.human,**fields))
        offer=run('worker_launch',dict(project=p,actor=self.agent,client='codex'))
        self.assertEqual([t['id'] for t in offer['batch']],[a,b])
        claim=self.claim(p,a)
        result=self.finish(p,a,claim,use_batch=True,claim_next=True,client='codex')
        self.assertTrue(result['next']['claim']['claimed']);self.assertEqual(result['next']['task']['id'],b)
        self.assertEqual(result['next']['claim']['file_lock_mode'],'checkout')
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])

    def test_quota_stop_rejects_new_scope_and_preserves_saved_file_plan(self):
        p,_=self.setup_project();t=self.task(p);claim=self.claim(p,t)
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',session_used_percent=93,weekly_used_percent=20))
        with self.assertRaises(ValueError):self.finish(p,t,claim,additional_files=['b.py'])
        task=run('task_get',dict(project=p,id=t));self.assertEqual(task['files'],['a.py']);self.assertEqual(task['status'],'TODO')
