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
