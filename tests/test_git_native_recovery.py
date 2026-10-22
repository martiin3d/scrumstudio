"""Mechanical Git recovery must not spend a model turn or alter foreign work."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import git_sync


class NativeGitRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir='data')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        self.git('init','-q','-b','main')
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        for name in ('a.txt','b.txt'):(self.root/name).write_text('base\n')
        self.git('add','.');self.git('commit','-qm','base')
        self.base=self.git('rev-parse','HEAD')
        self.settings={'git_push':'Never','git_mode':'Branches'}

    def git(self,*args):
        result=git_sync._git(self.root,*args,timeout=10)
        if result.returncode:raise AssertionError(result.stderr)
        return result.stdout.strip()

    def source(self):
        self.git('checkout','-qb','feature',self.base)
        self.git('config','branch.feature.scrum-parent','main')
        (self.root/'a.txt').write_text('verified\n')
        self.git('commit','-qam','verified')
        return self.git('rev-parse','HEAD')

    def test_foreign_git_environment_cannot_redirect_worktree_or_index(self):
        foreign=self.root/'foreign';foreign.mkdir()
        raw=git_sync._git
        raw(foreign,'init','-q')
        index=self.root/'foreign-index'
        with patch.dict(os.environ,{'GIT_DIR':str(foreign/'.git'),'GIT_WORK_TREE':str(foreign),'GIT_INDEX_FILE':str(index),'GIT_NAMESPACE':'foreign'}):
            self.assertEqual(self.git('rev-parse','--show-toplevel'),self.root.as_posix())
            (self.root/'a.txt').write_text('owned\n');self.git('add','a.txt')
            self.assertEqual(self.git('show',':a.txt'),'owned')
        self.assertFalse(index.exists())
        self.assertFalse((foreign/'.git/index').exists())

    def parent_chain(self):
        self.git('branch','middle',self.base);self.git('branch','foundation',self.base)
        self.git('config','branch.feature.scrum-parent','middle')
        self.git('config','branch.middle.scrum-parent','foundation')
        self.git('config','branch.foundation.scrum-parent','main')

    def stale_consumer(self):
        tip=self.source()
        self.git('checkout','-qb','dependency-one')
        (self.root/'b.txt').write_text('first dependency\n');self.git('commit','-qam','dependency one')
        self.git('checkout','-qb','dependency-two')
        (self.root/'c.txt').write_text('second dependency\n');self.git('add','c.txt');self.git('commit','-qm','dependency two')
        self.git('update-ref','refs/heads/main',self.git('rev-parse','HEAD'),self.base)
        task={'git_branch':'feature','allow_parent_refresh':True,'dependencies':[{'git_branch':'dependency-one'},{'git_branch':'dependency-two'}]}
        return tip,task,dict(self.settings,git_merge='Group')

    def test_claim_refreshes_all_missing_dependencies_in_one_native_fast_forward(self):
        _,task,settings=self.stale_consumer()
        result=git_sync.on_claim(self.root,settings,task)
        self.assertEqual(result['refreshed_from'],'main');self.assertNotIn('error',result)
        self.assertEqual(self.git('rev-parse','feature'),self.git('rev-parse','main'))
        self.assertEqual((self.root/'a.txt').read_text(),'verified\n')
        self.assertEqual((self.root/'b.txt').read_text(),'first dependency\n')
        self.assertEqual((self.root/'c.txt').read_text(),'second dependency\n')

    def test_stale_refresh_preserves_dirty_work_and_existing_staging(self):
        tip,task,settings=self.stale_consumer();self.git('checkout','-q','feature')
        (self.root/'a.txt').write_text('unfinished\n');(self.root/'b.txt').write_text('human staging\n');self.git('add','b.txt')
        index=(self.root/'.git/index').read_bytes()
        result=git_sync.on_claim(self.root,settings,task)
        self.assertIn('error',result);self.assertEqual(self.git('rev-parse','feature'),tip)
        self.assertEqual((self.root/'.git/index').read_bytes(),index)
        self.assertEqual((self.root/'a.txt').read_text(),'unfinished\n')
        git_sync.refresh_index_state(self.root)
        self.assertEqual((self.root/'.git/index').read_bytes(),index)

    @unittest.skipIf((git_sync.git_version() or (0,0))<git_sync.MERGE_TREE_MIN,'needs merge-tree')
    def test_metadata_only_divergence_preserves_history_and_exact_accepted_parent_tree(self):
        tip,task,settings=self.stale_consumer()
        tree=self.git('rev-parse','feature^{tree}')
        historical=self.git('commit-tree',tree,'-p',tip,'-p',self.base,'-m','Previous integration receipt')
        self.git('update-ref','refs/heads/feature',historical,tip)
        result=git_sync.on_claim(self.root,settings,task)
        self.assertEqual(result['refreshed_from'],'main')
        self.assertEqual(self.git('rev-parse','feature^{tree}'),self.git('rev-parse','main^{tree}'))
        self.assertTrue(git_sync.is_ancestor(self.root,historical,'feature'))

    def test_stale_refresh_requires_idle_branch_and_enabled_integration(self):
        tip,task,settings=self.stale_consumer()
        for changed,policy in [(dict(task,allow_parent_refresh=False),settings),(task,dict(settings,git_merge='Off'))]:
            result=git_sync.on_claim(self.root,policy,changed)
            self.assertIn('error',result);self.assertEqual(self.git('rev-parse','feature'),tip)

    def test_parent_refresh_never_discards_additional_consumer_files(self):
        _,task,settings=self.stale_consumer();self.git('checkout','-q','feature')
        (self.root/'extra.txt').write_text('unverified consumer output\n')
        self.git('add','extra.txt');self.git('commit','-qm','Additional consumer work')
        before=self.git('rev-parse','HEAD')
        result=git_sync.on_claim(self.root,settings,task)
        self.assertIn('error',result);self.assertEqual(self.git('rev-parse','HEAD'),before)
        self.assertEqual((self.root/'extra.txt').read_text(),'unverified consumer output\n')

    def test_existing_managed_worktree_refresh_preserves_canonical_staging(self):
        self.source();self.git('checkout','-q','main')
        task={'id':1,'git_branch':'feature','allow_parent_refresh':True}
        settings=dict(self.settings,git_mode='Worktrees',git_merge='Group',git_base_branch='main')
        initial=git_sync.on_claim(self.root,settings,task);work=Path(initial['worktree'])
        self.git('checkout','-qb','dependency','feature')
        (self.root/'b.txt').write_text('verified dependency\n');self.git('commit','-qam','Dependency')
        self.git('update-ref','refs/heads/main',self.git('rev-parse','HEAD'),self.base)
        (self.root/'a.txt').write_text('human staging\n');self.git('add','a.txt')
        index=(self.root/'.git/index').read_bytes()
        result=git_sync.on_claim(self.root,settings,dict(task,dependencies=[{'git_branch':'dependency'}]))
        self.assertEqual(result['refreshed_from'],'main');self.assertEqual(Path(result['worktree']),work)
        self.assertEqual((work/'b.txt').read_text(),'verified dependency\n')
        self.assertEqual((self.root/'.git/index').read_bytes(),index)

    def test_diverged_consumer_or_unpublished_dependency_keeps_recovery_boundary(self):
        tip,task,settings=self.stale_consumer()
        self.git('branch','unpublished',tip)
        # A prerequisite not accepted in main must not be pulled from its branch.
        self.git('checkout','-q','unpublished');(self.root/'d.txt').write_text('unverified\n')
        self.git('add','d.txt');self.git('commit','-qm','not accepted')
        result=git_sync.on_claim(self.root,settings,dict(task,dependencies=task['dependencies']+[{'git_branch':'unpublished'}]))
        self.assertIn('error',result);self.assertEqual(self.git('rev-parse','feature'),tip)
        self.git('checkout','-q','feature');(self.root/'a.txt').write_text('own divergent work\n');self.git('commit','-qam','diverged')
        before=self.git('rev-parse','HEAD')
        result=git_sync.on_claim(self.root,settings,task)
        self.assertIn('error',result);self.assertEqual(self.git('rev-parse','HEAD'),before)

    def test_verified_dependency_propagates_through_recorded_parent_chain(self):
        tip=self.source();self.parent_chain()
        result=git_sync.merge_dependency_checkpoint(self.root,self.settings,'feature')
        self.assertTrue(result['merged']);self.assertNotIn('error',result)
        self.assertEqual([r['target'] for r in result['ancestor_merges']],['foundation','main'])
        for branch in ('middle','foundation','main'):self.assertEqual(self.git('rev-parse',branch),tip)
        self.assertEqual(self.git('branch','--show-current'),'feature')

    @unittest.skipIf((git_sync.git_version() or (0,0))<git_sync.MERGE_TREE_MIN,'needs merge-tree')
    def test_unknown_intermediate_commits_do_not_propagate(self):
        self.source();self.parent_chain();self.git('checkout','-q','middle')
        (self.root/'b.txt').write_text('unverified work\n');self.git('commit','-qam','Unverified intermediate')
        self.git('checkout','-q','feature')
        result=git_sync.merge_dependency_checkpoint(self.root,self.settings,'feature')
        self.assertIn('additional history',result['error'])
        self.assertEqual(result['pending_target'],'foundation')
        self.assertEqual(self.git('rev-parse','main'),self.base)
        self.assertEqual(self.git('show','middle:b.txt'),'unverified work')

    def test_parent_cycle_is_rejected_before_merging(self):
        self.source();self.parent_chain();self.git('config','branch.foundation.scrum-parent','middle')
        result=git_sync.merge_dependency_checkpoint(self.root,self.settings,'feature')
        self.assertIn('cycle',result['error']);self.assertEqual(self.git('rev-parse','middle'),self.base)

    def test_required_locks_and_user_auth_configuration_are_preserved(self):
        with patch.dict(os.environ,{'GIT_INDEX_FILE':'foreign','GIT_CONFIG_GLOBAL':'identity-config','GIT_SSH_COMMAND':'custom-auth'}):
            with patch('git_sync.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
                git_sync._git(self.root,'status')
        env=run.call_args.kwargs['env']
        self.assertNotIn('GIT_INDEX_FILE',env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'],'identity-config')
        self.assertEqual(env['GIT_SSH_COMMAND'],'custom-auth')
        self.assertEqual(env['GIT_OPTIONAL_LOCKS'],'0')

    def test_already_integrated_target_preserves_dirty_worktree_and_staging(self):
        tip=self.source()
        self.git('update-ref','refs/heads/main',tip,self.base)
        self.git('checkout','-q','main')
        (self.root/'b.txt').write_text('human staging\n');self.git('add','b.txt')
        (self.root/'a.txt').write_text('unfinished edits\n')
        before=self.git('status','--porcelain');index=(self.root/'.git/index').read_bytes()
        result=git_sync.merge_completed(self.root,self.settings,'feature')
        self.assertTrue(result['already_integrated']);self.assertTrue(result['merged'])
        self.assertEqual(self.git('rev-parse','HEAD'),tip)
        self.assertEqual(self.git('status','--porcelain'),before)
        self.assertEqual((self.root/'.git/index').read_bytes(),index)

    def test_ancestry_failure_does_not_become_a_merge_or_noop(self):
        self.source();raw=git_sync._git
        def fail(root,*args,**kwargs):
            if args[0]=='merge-base':return subprocess.CompletedProcess(args,128,'','missing object')
            return raw(root,*args,**kwargs)
        with patch('git_sync._git',side_effect=fail):
            result=git_sync.merge_completed(self.root,self.settings,'feature')
        self.assertFalse(result['merged']);self.assertIn('Cannot verify Git ancestry',result['error'])
        self.assertEqual(self.git('rev-parse','main'),self.base)

    def test_already_integrated_still_reports_failed_publication(self):
        tip=self.source();self.git('update-ref','refs/heads/main',tip,self.base)
        self.git('checkout','-q','main');(self.root/'b.txt').write_text('human edits\n')
        raw=git_sync._git;pushes=[]
        def fail(root,*args,**kwargs):
            if args[0]=='push':
                pushes.append(args)
                return subprocess.CompletedProcess(args,1,'','remote unavailable')
            return raw(root,*args,**kwargs)
        with patch('git_sync._git',side_effect=fail):
            result=git_sync.merge_completed(self.root,dict(self.settings,git_push='Group'),'feature')
        self.assertTrue(result['already_integrated']);self.assertFalse(result['pushed'])
        self.assertIn('target push failed',result['error']);self.assertEqual(len(pushes),1)
        self.assertEqual((self.root/'b.txt').read_text(),'human edits\n')

    @unittest.skipIf((git_sync.git_version() or (0,0))<git_sync.MERGE_TREE_MIN,'needs merge-tree')
    def test_concurrent_target_advance_retries_locally_with_fixed_source(self):
        self.git('checkout','-qb','concurrent')
        (self.root/'b.txt').write_text('concurrent target\n');self.git('commit','-qam','advance')
        concurrent=self.git('rev-parse','HEAD');tip=self.source();raw=git_sync._git;updates=[]
        def race(root,*args,**kwargs):
            if args[:2]==('update-ref','refs/heads/main'):
                updates.append(args)
                if len(updates)==1:raw(root,'update-ref','refs/heads/main',concurrent,self.base)
            return raw(root,*args,**kwargs)
        with patch('git_sync._git',side_effect=race):
            result=git_sync.merge_completed(self.root,self.settings,'feature')
        self.assertTrue(result['merged']);self.assertEqual(result['source_sha'],tip)
        self.assertEqual(len(updates),2)
        self.assertEqual(self.git('show','main:a.txt'),'verified')
        self.assertEqual(self.git('show','main:b.txt'),'concurrent target')
        self.assertEqual(self.git('branch','--show-current'),'feature')
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_non_race_update_failure_does_not_retry(self):
        self.source();raw=git_sync._git;updates=[]
        def fail(root,*args,**kwargs):
            if args[0]=='update-ref':
                updates.append(args)
                return subprocess.CompletedProcess(args,128,'','permission denied')
            return raw(root,*args,**kwargs)
        with patch('git_sync._git',side_effect=fail):result=git_sync.merge_completed(self.root,self.settings,'feature')
        self.assertFalse(result['merged']);self.assertEqual(len(updates),1)
        self.assertEqual(self.git('rev-parse','main'),self.base)

    @unittest.skipIf((git_sync.git_version() or (0,0))<git_sync.MERGE_TREE_MIN,'needs merge-tree')
    def test_repeated_target_races_are_bounded(self):
        self.source();raw=git_sync._git;updates=[]
        def race(root,*args,**kwargs):
            if args[:2]==('update-ref','refs/heads/main'):
                updates.append(args)
                old=raw(root,'rev-parse','main').stdout.strip()
                tree=raw(root,'rev-parse','main^{tree}').stdout.strip()
                new=raw(root,'commit-tree',tree,'-p',old,'-m','Concurrent '+str(len(updates))).stdout.strip()
                raw(root,'update-ref','refs/heads/main',new,old)
            return raw(root,*args,**kwargs)
        with patch('git_sync._git',side_effect=race):result=git_sync.merge_completed(self.root,self.settings,'feature')
        self.assertFalse(result['merged']);self.assertEqual(len(updates),3)
        self.assertNotIn('retryable',result)
        self.assertEqual(self.git('show','main:a.txt'),'base')

    @unittest.skipIf((git_sync.git_version() or (0,0))<git_sync.MERGE_TREE_MIN,'needs merge-tree')
    def test_target_rewind_is_not_automatically_retried(self):
        self.source();tree=self.git('rev-parse','main^{tree}')
        before=self.git('commit-tree',tree,'-p',self.base,'-m','Target advanced')
        self.git('update-ref','refs/heads/main',before,self.base)
        raw=git_sync._git;updates=[]
        def rewind(root,*args,**kwargs):
            if args[:2]==('update-ref','refs/heads/main'):
                updates.append(args);raw(root,'update-ref','refs/heads/main',self.base,before)
            return raw(root,*args,**kwargs)
        with patch('git_sync._git',side_effect=rewind):result=git_sync.merge_completed(self.root,self.settings,'feature')
        self.assertFalse(result['merged']);self.assertEqual(len(updates),1)
        self.assertEqual(self.git('rev-parse','main'),self.base)


if __name__=='__main__':unittest.main()
