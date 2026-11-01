import json
import unittest
from unittest.mock import patch
import git_paused
import git_sync
import test_git_paused as fixtures

class StagedPauseTest(unittest.TestCase):
    setUp=fixtures.PausedGitTest.setUp
    git=fixtures.PausedGitTest.git
    def park(self,files=None):return git_paused.pause(self.root,'partial',files or ['one.txt'],['TEST-1'],retain_unplanned=True)
    def staged(self):
        (self.root/'one.txt').write_text('staged draft');self.git('add','one.txt');(self.root/'one.txt').write_text('later working draft')
    def test_preserves_index_and_working_versions_independently(self):
        self.staged();index=self.git('write-tree');report=self.park();self.assertTrue(report);self.assertEqual(git_sync.state(self.root),('partial',[]))
        git_paused.restore(self.root,'partial','TEST-1');self.assertEqual(self.git('write-tree'),index);self.assertEqual(self.git('show',':one.txt'),'staged draft');self.assertEqual((self.root/'one.txt').read_text(),'later working draft')
        self.assertIsNone(git_paused.restore(self.root,'partial','TEST-1'))
    def test_foreign_staging_is_never_cleared(self):
        self.staged();(self.root/'two.txt').write_text('human stage');self.git('add','two.txt');index=self.git('write-tree')
        self.assertIsNone(self.park());self.assertEqual(self.git('write-tree'),index);self.assertEqual((self.root/'one.txt').read_text(),'later working draft')
    def test_default_policy_still_refuses_staging(self):
        self.staged();self.assertIsNone(git_paused.pause(self.root,'partial',['one.txt'],['TEST-1']))
    def test_staged_change_with_working_bytes_equal_head_is_preserved(self):
        original=(self.root/'one.txt').read_bytes();self.staged();(self.root/'one.txt').write_bytes(original);index=self.git('write-tree')
        self.assertTrue(self.park());git_paused.restore(self.root,'partial','TEST-1');self.assertEqual(self.git('write-tree'),index);self.assertEqual((self.root/'one.txt').read_bytes(),original)
    def test_staged_addition_deletion_and_unplanned_original(self):
        (self.root/'new.txt').write_text('staged new');self.git('add','new.txt');(self.root/'new.txt').write_text('working new')
        self.git('rm','one.txt');(self.root/'foreign.txt').write_text('foreign draft');index=self.git('write-tree')
        report=self.park(['one.txt','new.txt']);self.assertTrue(report);self.assertEqual(git_sync.state(self.root),('partial',[]))
        git_paused.restore(self.root,'partial','TEST-1');self.assertEqual(self.git('write-tree'),index);self.assertEqual((self.root/'new.txt').read_text(),'working new');self.assertFalse((self.root/'one.txt').exists());self.assertFalse((self.root/'foreign.txt').exists())
    def test_parent_advance_preserves_new_parent_and_owned_staging(self):
        self.staged();self.park();(self.root/'two.txt').write_text('accepted parent');self.git('commit','-qam','Parent update')
        git_paused.restore(self.root,'partial','TEST-1');self.assertEqual(self.git('show',':one.txt'),'staged draft');self.assertEqual((self.root/'one.txt').read_text(),'later working draft');self.assertEqual((self.root/'two.txt').read_text(),'accepted parent')
    def test_index_race_is_not_cleared(self):
        self.staged();original=git_paused.save
        def race(path,data):
            result=original(path,data)
            if data.get('state')=='saved':(self.root/'two.txt').write_text('new stage');self.git('add','two.txt')
            return result
        with patch.object(git_paused,'save',side_effect=race):
            with self.assertRaisesRegex(ValueError,'Staging changed'):self.park()
        self.assertEqual(self.git('show',':two.txt'),'new stage');self.assertEqual((self.root/'one.txt').read_text(),'later working draft')

class StagedLaunchTest(unittest.TestCase):
    import test_git_mixed_pause as fixture
    setUp=fixture.MixedLaunchTest.setUp
    tearDown=fixture.MixedLaunchTest.tearDown
    project=fixture.MixedLaunchTest.project
    repo=fixture.MixedLaunchTest.repo
    scenario=fixture.MixedLaunchTest.scenario
    def test_supervisor_parks_owned_staging_without_recovery_and_restores_on_claim(self):
        from store import run
        project,root,git,task,other,branch=self.scenario()
        git('add','a.py');(root/'a.py').write_text('later owned version')
        offer=run('worker_launch',dict(project=project,actor=self.agent,client='codex'));self.assertTrue(offer['eligible']);self.assertEqual(offer['id'],other);self.assertNotIn('checkout_recovery',offer)
        self.assertEqual(git('diff','--cached','--name-only'),'')
        claim=run('task_claim',dict(project=project,id=other,actor=self.agent));(root/'b.py').write_text('accepted prerequisite')
        self.assertEqual(run('work_finish',dict(project=project,id=other,actor=self.agent,token=claim['token'],evidence='Verified'))['completed']['status'],'Done')
        run('task_update',dict(project=project,id=task,actor=self.human,status='TODO',blocked_reason=''))
        resumed=run('task_claim',dict(project=project,id=task,actor=self.agent));self.assertTrue(resumed['claimed']);self.assertEqual(git('show',':a.py'),'partial owned');self.assertEqual((root/'a.py').read_text(),'later owned version');self.assertFalse((root/'human.cs').exists())
