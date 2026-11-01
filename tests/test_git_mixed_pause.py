import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import git_paused
import git_sync
import test_git_paused as basic
import test_low_turns as fixtures
from store import run

class MixedCheckpointTest(unittest.TestCase):
    setUp=basic.PausedGitTest.setUp
    git=basic.PausedGitTest.git
    def park(self):
        return git_paused.pause(self.root,'partial',['one.txt','draft.bin'],['TEST-1'],retain_unplanned=True)
    def dirty(self):
        (self.root/'one.txt').write_bytes(b'owned\r\n\x00binary\xff')
        (self.root/'draft.bin').write_bytes(b'owned draft')
        (self.root/'two.txt').write_text('foreign authored change')
        (self.root/'foreign.cs').write_bytes(b'unrelated source')
    def test_mixed_snapshot_restores_only_owned_and_retains_foreign_bytes(self):
        self.git('config','diff.noprefix','true')
        self.dirty();before=self.git('rev-parse','HEAD');report=self.park()
        self.assertEqual(report['files'],2);self.assertEqual(report['unplanned_retained'],2)
        self.assertEqual(git_sync.state(self.root),('partial',[]));self.assertEqual(self.git('rev-parse','HEAD'),before)
        data=json.loads((self.root/report['manifest']).read_text());directory=(self.root/report['manifest']).parent
        self.assertEqual(set(data['files']),{'one.txt','draft.bin'});self.assertEqual(set(data['unplanned']),{'two.txt','foreign.cs'})
        self.assertEqual((directory/data['unplanned']['two.txt']['blob']).read_text(),'foreign authored change')
        self.assertEqual((directory/'unplanned-originals'/data['unplanned']['foreign.cs']['blob']).read_bytes(),b'unrelated source')
        inputs=git_paused.preserved_inputs(self.root,'TEST-1')
        self.assertEqual({x['path'] for x in inputs['items']},{'one.txt','draft.bin'})
        git_paused.restore(self.root,'partial','TEST-1')
        self.assertEqual((self.root/'one.txt').read_bytes(),b'owned\r\n\x00binary\xff');self.assertEqual((self.root/'draft.bin').read_bytes(),b'owned draft')
        self.assertEqual((self.root/'two.txt').read_text(),'other\n');self.assertFalse((self.root/'foreign.cs').exists())
        self.assertEqual(self.git('diff','--cached','--name-only'),'');self.assertEqual(self.git('stash','list'),'')
        self.assertIsNone(git_paused.restore(self.root,'partial','TEST-1'))
    def test_new_parent_version_of_foreign_file_survives_resume(self):
        self.dirty();self.park();(self.root/'two.txt').write_text('new accepted parent')
        self.git('commit','-qam','Accepted parent update');git_paused.restore(self.root,'partial','TEST-1')
        self.assertEqual((self.root/'two.txt').read_text(),'new accepted parent');self.assertEqual((self.root/'one.txt').read_bytes(),b'owned\r\n\x00binary\xff')
        self.assertEqual(self.git('diff','--cached','--name-only'),'')
    def test_owned_conflict_preserves_private_backup_and_foreign_parent(self):
        self.dirty();report=self.park();(self.root/'one.txt').write_text('conflicting accepted version');self.git('commit','-qam','Parent conflict')
        with self.assertRaisesRegex(ValueError,'conflict'):git_paused.restore(self.root,'partial','TEST-1')
        self.assertTrue((self.root/report['manifest']).exists());self.assertFalse((self.root/'foreign.cs').exists())
    def test_mixed_snapshot_still_refuses_staging_and_oversize(self):
        self.dirty();self.git('add','two.txt');self.assertIsNone(self.park());self.assertTrue((self.root/'foreign.cs').exists())
        self.git('restore','--staged','two.txt')
        with (self.root/'foreign.cs').open('wb') as f:f.truncate(64*1024*1024+1)
        self.assertIsNone(self.park());self.assertFalse(git_paused.pending(self.root,'partial'))
    def test_interrupted_cleaning_reconciles_both_backup_scopes(self):
        self.dirty();report=self.park();pointer=git_paused.folder(self.root,'partial')/'manifest.json';meta=json.loads(pointer.read_text());meta['state']='cleaning';git_paused.save(pointer,meta)
        git_paused.restore(self.root,'partial','TEST-1');self.assertEqual((self.root/'one.txt').read_bytes(),b'owned\r\n\x00binary\xff');self.assertFalse((self.root/'foreign.cs').exists())
    def test_unplanned_copy_race_never_cleans_originals(self):
        self.dirty();original=git_paused.save
        def race(path,data):
            result=original(path,data)
            if data.get('state')=='saved':(self.root/'foreign.cs').write_text('new human change')
            return result
        with patch.object(git_paused,'save',side_effect=race):
            with self.assertRaisesRegex(ValueError,'changed'):self.park()
        self.assertEqual((self.root/'foreign.cs').read_text(),'new human change');self.assertEqual((self.root/'one.txt').read_bytes(),b'owned\r\n\x00binary\xff')

    @unittest.skipUnless(os.name=='nt','Windows case-insensitive task paths')
    def test_declared_path_casing_does_not_turn_owned_drafts_into_foreign(self):
        self.dirty();report=git_paused.pause(self.root,'partial',['ONE.TXT','DRAFT.BIN'],['TEST-1'],retain_unplanned=True)
        self.assertEqual(report['files'],2);self.assertEqual(report['unplanned_retained'],2)
        git_paused.restore(self.root,'partial','TEST-1');self.assertEqual((self.root/'one.txt').read_bytes(),b'owned\r\n\x00binary\xff')
    def test_owned_deletion_and_space_paths_restore_without_foreign_file(self):
        (self.root/'name with spaces.txt').write_text('base');self.git('add','name with spaces.txt');self.git('commit','-qm','Fixture space path')
        (self.root/'one.txt').unlink();(self.root/'name with spaces.txt').write_text('updated space path');(self.root/'foreign.cs').write_text('foreign')
        git_paused.pause(self.root,'partial',['one.txt','name with spaces.txt'],['TEST-1'],retain_unplanned=True)
        git_paused.restore(self.root,'partial','TEST-1');self.assertFalse((self.root/'one.txt').exists());self.assertEqual((self.root/'name with spaces.txt').read_text(),'updated space path');self.assertFalse((self.root/'foreign.cs').exists())

class MixedLaunchTest(unittest.TestCase):
    setUp=fixtures.GitAutomationTest.setUp
    tearDown=fixtures.GitAutomationTest.tearDown
    project=fixtures.GitAutomationTest.project
    repo=fixtures.GitAutomationTest.repo
    def scenario(self,**policy):
        project,root=self.project();git=self.repo(root)
        run('project_settings',dict(project=project,actor=self.human,git_mode='Branches',git_base_branch='main',git_merge='Task',git_push='Never',git_pause_blocked=True,autonomy_mode='Autonomous',max_parallel_agents=1,file_lock_policy='Auto',usage_guard=False,**policy))
        task=run('task_create',dict(project=project,title='Blocked partial result',files=['a.py'],**fixtures.SPEC))['id']
        other=run('task_create',dict(project=project,title='Ready prerequisite',files=['b.py'],**fixtures.SPEC))['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));(root/'a.py').write_text('partial owned');(root/'human.cs').write_text('foreign original')
        run('task_update',dict(project=project,id=task,actor=self.agent,token=claim['token'],status='Blocked',blocked_reason='External prerequisite'))
        return project,root,git,task,other,claim['git']['branch']
    def test_native_launch_parks_mixed_files_without_recovery_task_and_resume_keeps_scope(self):
        project,root,git,task,other,branch=self.scenario()
        offered=run('worker_launch',dict(project=project,actor=self.agent,client='codex'));self.assertTrue(offered['eligible']);self.assertEqual(offered['id'],other);self.assertNotIn('checkout_recovery',offered)
        self.assertFalse((root/'human.cs').exists());self.assertEqual((root/'a.py').read_text(),'a\n')
        self.assertEqual(len(run('task_search',dict(project=project,limit=10))['items']),2)
        held=run('task_claim',dict(project=project,id=other,actor=self.agent));(root/'b.py').write_text('prerequisite accepted')
        result=run('work_finish',dict(project=project,id=other,actor=self.agent,token=held['token'],evidence='Targeted prerequisite passed'))
        self.assertEqual(result['completed']['status'],'Done')
        run('task_update',dict(project=project,id=task,actor=self.human,status='TODO',blocked_reason=''))
        resumed=run('task_claim',dict(project=project,id=task,actor=self.agent));self.assertTrue(resumed['claimed']);self.assertEqual((root/'a.py').read_text(),'partial owned');self.assertEqual((root/'b.py').read_text(),'prerequisite accepted');self.assertFalse((root/'human.cs').exists())
        self.assertEqual(run('task_get',dict(project=project,id=task))['files'],['a.py'])
    def test_mixed_launch_requires_autonomy_single_worker_and_private_checkpoint_opt_in(self):
        for field,value in [('autonomy_mode','Standard'),('max_parallel_agents',2),('git_pause_blocked',False)]:
            with self.subTest(field=field):
                project,root,git,task,other,branch=self.scenario()
                run('project_settings',dict(project=project,actor=self.human,**{field:value}))
                report=run('worker_launch',dict(project=project,actor=self.agent,client='codex'))
                self.assertFalse(report.get('eligible'));self.assertEqual((root/'human.cs').read_text(),'foreign original');self.assertFalse(git_paused.pending(root,branch))
    def test_stop_does_not_park_or_launch(self):
        project,root,git,task,other,branch=self.scenario();run('project_control',dict(project=project,actor=self.human,action='stop'))
        report=run('worker_launch',dict(project=project,actor=self.agent,client='codex'));self.assertFalse(report.get('eligible'));self.assertTrue((root/'human.cs').exists());self.assertFalse(git_paused.pending(root,branch))

    def test_human_live_claim_prevents_mixed_checkout_parking(self):
        project,root,git,task,other,branch=self.scenario()
        human_task=run('task_create',dict(project=project,title='Human checkout inspection',category='Git recovery',files=['human.cs'],**fixtures.SPEC))['id']
        claim=run('task_claim',dict(project=project,id=human_task,actor=self.human));self.assertTrue(claim['claimed'])
        report=run('worker_launch',dict(project=project,actor=self.agent,client='codex'));self.assertFalse(report.get('eligible'));self.assertTrue((root/'human.cs').exists());self.assertFalse(git_paused.pending(root,branch))
