import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch
import test_storage
import test_low_turns
SPEC=test_low_turns.SPEC
from store import run


import git_sync as _git_sync
NEEDS_MERGE_TREE=unittest.skipIf((_git_sync.git_version() or (0,0))<_git_sync.MERGE_TREE_MIN,"git_merge needs git 2.38+ (merge-tree --write-tree)")

class WorktreeTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    def repo(self,folder):
        def git(*args):
            return subprocess.run(['git','-C',str(folder),*args],capture_output=True,text=True,check=True).stdout.strip()
        git('init','-q','-b','main');git('config','user.email','fixture@example.invalid');git('config','user.name','Fixture')
        (folder/'.gitignore').write_text('.scrum/\n')
        (folder/'a.py').write_text('a\n');(folder/'b.py').write_text('b\n')
        git('add','.');git('commit','-qm','Fixture base')
        return git

    def setup_repo(self,**more):
        project,folder=self.project();git=self.repo(folder)
        run('project_settings',dict(project=project,actor=self.human,git_mode='Worktrees',git_base_branch='main',git_push='Never',max_parallel_agents=5,**more))
        return project,folder,git

    def task(self,project,title,file,**more):
        return run('task_create',dict(project=project,title=title,files=[file],**SPEC,**more))['id']

    def other(self,project):
        actor=run('user_register',dict(identity='agent:second',name='Second',kind='agent'))['id']
        run('agent_checkpoint',dict(project=project,actor=actor,client='codex',session_used_percent=10,weekly_used_percent=20))
        return actor

    def test_parallel_claims_preserve_dirty_canonical_checkout_and_distinct_indexes(self):
        project,folder,git=self.setup_repo()
        (folder/'a.py').write_text('human');git('add','a.py')
        first=self.task(project,'First','a.py');second=self.task(project,'Second','b.py')
        one=run('task_claim',dict(project=project,id=first,actor=self.agent))
        two=run('task_claim',dict(project=project,id=second,actor=self.other(project)))
        left=Path(one['git']['worktree']);right=Path(two['git']['worktree'])
        self.assertNotEqual(left,right);self.assertEqual(git('branch','--show-current'),'main')
        self.assertEqual((left/'a.py').read_text(),'a\n');self.assertEqual((right/'a.py').read_text(),'a\n')
        (left/'a.py').write_text('worker one');(right/'b.py').write_text('worker two')
        import git_sync
        git_sync._git(left,'add','a.py')
        self.assertEqual(git_sync._git(right,'diff','--cached','--name-only').stdout,'')
        self.assertEqual(git('show',':a.py'),'human')
        changes=run('task_changes',dict(project=project,id=first))
        self.assertIn('worker one',changes['diff']);self.assertNotIn('human',changes['diff'])
        from storage import portable_file
        self.assertEqual(portable_file(str(left/'a.py'),folder),'a.py')
        import coordination
        self.assertEqual(coordination.canonical_paths(folder,[str(left/'a.py')]),coordination.canonical_paths(folder,['a.py']))

    def test_branches_recovery_is_offered_on_dirty_checkout_but_waits_for_live_work(self):
        project,folder,git=self.setup_repo()
        run('project_settings',dict(project=project,actor=self.human,git_mode='Branches'))
        task=self.task(project,'Recover integration','a.py',category='Git recovery')
        (folder/'b.py').write_text('human edits');git('add','b.py')
        offer=run('worker_launch',dict(project=project,actor=self.agent))
        self.assertTrue(offer['eligible']);self.assertEqual(offer['id'],task)
        claim=run('task_claim',dict(project=project,actor=self.agent,id=task))
        self.assertTrue(claim['claimed']);self.assertEqual(git('show',':b.py'),'human edits')
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='pause'))
        run('worker_launch',dict(project=project,actor=self.agent,action='release'))
        git('reset','--hard','HEAD')  # Only the isolated fixture's disposable edits.
        other=self.other(project)
        normal=self.task(project,'Active outcome','b.py')
        run('task_claim',dict(project=project,actor=other,id=normal))
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',session_used_percent=10,weekly_used_percent=20))
        selected=run('next_work',dict(project=project,actor=self.agent))
        self.assertIsNone(selected['task'])

    def test_branches_filter_respects_own_live_task_with_spare_wip_capacity(self):
        project,folder,git=self.setup_repo()
        run('project_settings',dict(project=project,actor=self.human,git_mode='Branches',max_active_per_agent=2))
        first=self.task(project,'Current','a.py');self.task(project,'Different branch','b.py')
        run('task_claim',dict(project=project,actor=self.agent,id=first))
        selected=run('next_work',dict(project=project,actor=self.agent,exclude_ids=[first]))
        self.assertIsNone(selected['task']);self.assertEqual(selected['branch_skipped'],1)

    def test_pause_and_resume_reuses_partial_work_without_touching_main(self):
        project,folder,git=self.setup_repo()
        task=self.task(project,'Resume','a.py')
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));work=Path(claim['git']['worktree'])
        (work/'a.py').write_text('partial outcome')
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='pause',summary='Continue partial edit'))
        self.assertIn('Changed planned files: a.py',run('task_get',dict(project=project,id=task))['continuity'])
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',session_used_percent=10,weekly_used_percent=20))
        again=run('task_claim',dict(project=project,id=task,actor=self.agent))
        self.assertEqual(again['git']['worktree'],str(work));self.assertEqual((work/'a.py').read_text(),'partial outcome')
        self.assertEqual((folder/'a.py').read_text(),'a\n')
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='pause',summary='Resume after restore'))
        with self.assertRaisesRegex(ValueError,'saved workspaces'):
            run('project_settings',dict(project=project,actor=self.human,git_mode='Branches'))
        code=run('task_get',dict(project=project,id=task))['code']
        run('project_export',dict(project=project))
        run('project_restore',dict(project=project,confirm=True))
        restored=run('task_get',dict(project=project,code=code))
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',session_used_percent=10,weekly_used_percent=20))
        resumed=run('task_claim',dict(project=project,id=restored['id'],actor=self.agent))
        self.assertEqual(resumed['git']['worktree'],str(work))
        self.assertEqual((work/'a.py').read_text(),'partial outcome')

    @NEEDS_MERGE_TREE
    def test_parallel_completed_work_merges_into_clean_canonical_parent(self):
        project,folder,git=self.setup_repo(git_merge='Group')
        first=self.task(project,'One','a.py');second=self.task(project,'Two','b.py');other=self.other(project)
        one=run('task_claim',dict(project=project,id=first,actor=self.agent))
        two=run('task_claim',dict(project=project,id=second,actor=other))
        (Path(one['git']['worktree'])/'a.py').write_text('one')
        (Path(two['git']['worktree'])/'b.py').write_text('two')
        for task,actor,claim in [(first,self.agent,one),(second,other,two)]:
            result=run('work_finish',dict(project=project,id=task,actor=actor,token=claim['token'],evidence='Fixture verified'))
            self.assertTrue(result['completed']['git']['merge']['merged'])
        self.assertEqual(git('branch','--show-current'),'main')
        self.assertEqual((folder/'a.py').read_text(),'one');self.assertEqual((folder/'b.py').read_text(),'two')
        self.assertEqual(git('status','--porcelain'),'')

    @NEEDS_MERGE_TREE
    def test_cleanup_waits_for_cli_exit_and_reopened_task_gets_parent(self):
        project,folder,git=self.setup_repo(git_merge='Group',git_cleanup=True)
        task=self.task(project,'Cleanup','a.py')
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));work=Path(claim['git']['worktree'])
        (work/'a.py').write_text('completed')
        result=run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified'))
        self.assertTrue(result['completed']['git']['cleanup']['pending']);self.assertTrue(work.exists())
        result=run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='offline',run_exit_code=0))
        self.assertTrue(result['git_cleanup'][0]['removed']);self.assertFalse(work.exists())
        self.assertEqual(git('show','main:a.py'),'completed')
        import git_sync
        self.assertNotEqual(git_sync._git(folder,'show-ref','--verify','refs/heads/'+claim['git']['branch']).returncode,0)
        run('task_update',dict(project=project,id=task,actor=self.human,status='TODO'))
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',session_used_percent=10,weekly_used_percent=20))
        resumed=run('task_claim',dict(project=project,id=task,actor=self.agent))
        self.assertEqual(resumed['git']['parent'],'main');self.assertEqual((work/'a.py').read_text(),'completed')

    @NEEDS_MERGE_TREE
    def test_cleanup_preserves_untracked_and_ignored_files(self):
        project,folder,git=self.setup_repo(git_merge='Group',git_cleanup=True)
        task=self.task(project,'Keep files','a.py')
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));work=Path(claim['git']['worktree'])
        (work/'a.py').write_text('completed');(work/'.scrum').mkdir();(work/'.scrum/private').write_text('keep')
        run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified'))
        result=run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='offline',run_exit_code=0))
        self.assertFalse(result['git_cleanup'][0]['removed']);self.assertEqual((work/'.scrum/private').read_text(),'keep')

    @NEEDS_MERGE_TREE
    def test_cleanup_keeps_parent_needed_by_another_branch(self):
        project,folder,git=self.setup_repo(git_merge='Group',git_cleanup=True)
        task=self.task(project,'Parent','a.py');claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
        work=Path(claim['git']['worktree']);(work/'a.py').write_text('completed')
        run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified'))
        git('branch','dependent',claim['git']['branch']);git('config','branch.dependent.scrum-parent',claim['git']['branch'])
        result=run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='offline',run_exit_code=0))
        self.assertIn('origin',result['git_cleanup'][0]['reason']);self.assertTrue(work.exists())

    @NEEDS_MERGE_TREE
    def test_cleanup_defers_for_new_related_work_and_rechecks_after_later_exit(self):
        project,folder,git=self.setup_repo(git_merge='Group',git_cleanup=True)
        task=self.task(project,'First related','a.py')
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));work=Path(claim['git']['worktree'])
        (work/'a.py').write_text('completed')
        run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified'))
        dependent=self.task(project,'Pending dependent','b.py',dependencies=[task])
        result=run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='offline',run_exit_code=0))
        self.assertIn('related unfinished',result['git_cleanup'][0]['reason']);self.assertTrue(work.exists())
        run('task_update',dict(project=project,id=dependent,actor=self.human,status='Done',evidence='No implementation needed'))
        other=self.other(project)
        result=run('agent_checkpoint',dict(project=project,actor=other,client='codex',action='offline',run_exit_code=0))
        self.assertTrue(any(r['removed'] for r in result['git_cleanup']));self.assertFalse(work.exists())

    def test_related_work_includes_context_and_transitive_dependencies(self):
        import git_worktrees
        rows=[dict(id=1,uid='a',number=1,title='First',context_group='shared',dependencies='[]',status='Done'),
              dict(id=2,uid='b',number=2,title='Second',context_group='shared',dependencies='[]',status='Done'),
              dict(id=3,uid='c',number=3,title='Third',context_group='',dependencies='[2]',status='Blocked')]
        self.assertTrue(git_worktrees.related_pending(self.temp.name,'scrum/shared',rows,{'git_branch_prefix':'scrum/'},'TES'))
        rows[2]['status']='Done'
        self.assertFalse(git_worktrees.related_pending(self.temp.name,'scrum/shared',rows,{'git_branch_prefix':'scrum/'},'TES'))

    def test_context_group_is_sequential_and_file_conflicts_remain_central(self):
        project,folder,git=self.setup_repo();other=self.other(project)
        first=self.task(project,'One','a.py',context_group='shared');second=self.task(project,'Two','b.py',context_group='shared')
        one=run('task_claim',dict(project=project,id=first,actor=self.agent))
        refused=run('task_claim',dict(project=project,id=second,actor=other))
        self.assertFalse(refused['claimed']);self.assertIn('context-group',refused['branch_busy']['reason'])
        overlap=self.task(project,'Overlap','a.py')
        refused=run('task_claim',dict(project=project,id=overlap,actor=other))
        self.assertFalse(refused['claimed']);self.assertIn('conflicts',refused)
        run('work_finish',dict(project=project,id=first,actor=self.agent,token=one['token'],evidence='No edit needed'))
        two=run('task_claim',dict(project=project,id=second,actor=other))
        self.assertEqual(one['git']['worktree'],two['git']['worktree'])

    def test_batches_do_not_cross_worktree_branches_and_mode_change_waits(self):
        project,folder,git=self.setup_repo()
        first=self.task(project,'One','a.py');second=self.task(project,'Two','a.py')
        offer=run('worker_launch',dict(project=project,actor=self.agent,client='codex',action='reserve',id=first))
        self.assertEqual([t['id'] for t in offer['batch']],[first])
        claim=run('task_claim',dict(project=project,id=first,actor=self.agent))
        with self.assertRaisesRegex(ValueError,'pause active'):
            run('project_settings',dict(project=project,actor=self.human,git_mode='Branches'))
