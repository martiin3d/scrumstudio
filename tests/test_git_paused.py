"""Blocked work can yield one checkout without publishing or losing its edits."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import git_paused
import git_sync


class PausedGitTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='data');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.git('init','-q','-b','main');self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        (self.root/'.gitignore').write_text('.scrum/\n')
        (self.root/'one.txt').write_bytes(b'base\n')
        (self.root/'two.txt').write_bytes(b'other\n')
        self.git('add','.');self.git('commit','-qm','base')
        self.base=self.git('rev-parse','HEAD');self.git('checkout','-qb','partial')

    def git(self,*args):
        return git_paused.checked(self.root,*args)

    def pause(self,files=('one.txt','new file.bin')):
        with git_sync.checkout_lock(self.root):return git_paused.pause(self.root,'partial',files,['TEST-1'])

    def test_roundtrip_preserves_bytes_deletions_and_untracked_without_publication(self):
        raw=b'partial\r\n\x00binary\xff';(self.root/'one.txt').write_bytes(raw)
        (self.root/'new file.bin').write_bytes(b'\x00\xffnew')
        (self.root/'two.txt').unlink()
        self.assertEqual(self.pause(['one.txt','two.txt','new file.bin'])['files'],3)
        self.assertEqual(git_sync.state(self.root),('partial',[]))
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)
        self.assertEqual(self.git('stash','list'),'')
        self.assertEqual(self.git('for-each-ref','--format=%(refname)','refs/heads').splitlines(),['refs/heads/main','refs/heads/partial'])
        self.git('checkout','-q','main');self.git('checkout','-q','partial')
        self.assertEqual(git_paused.restore(self.root,'partial')['tasks'],['TEST-1'])
        self.assertEqual((self.root/'one.txt').read_bytes(),raw)
        self.assertFalse((self.root/'two.txt').exists())
        self.assertEqual((self.root/'new file.bin').read_bytes(),b'\x00\xffnew')
        self.assertEqual(self.git('diff','--cached','--name-only'),'')
        self.assertIsNone(git_paused.restore(self.root,'partial'))

    def test_staged_or_unrelated_work_is_never_removed(self):
        (self.root/'one.txt').write_text('human');self.git('add','one.txt')
        (self.root/'one.txt').write_text('later')
        self.assertIsNone(self.pause());self.assertEqual(self.git('show',':one.txt'),'human')
        self.git('reset','-q');(self.root/'two.txt').write_text('unrelated')
        self.assertIsNone(self.pause());self.assertEqual((self.root/'one.txt').read_text(),'later')
        self.assertEqual((self.root/'two.txt').read_text(),'unrelated')

    def test_resume_claim_restores_without_setting_or_extra_call(self):
        (self.root/'one.txt').write_text('resume me');self.pause()
        self.git('checkout','-q','main')
        report=git_sync.on_claim(self.root,{'git_mode':'Branches'},{'git_branch':'partial'})
        self.assertIn('restored_checkpoint',report)
        self.assertEqual((self.root/'one.txt').read_text(),'resume me')

    def test_parent_advance_merges_only_nonconflicting_changes(self):
        (self.root/'one.txt').write_text('partial');self.pause()
        (self.root/'two.txt').write_text('accepted parent');self.git('commit','-qam','parent update')
        git_paused.restore(self.root,'partial')
        self.assertEqual((self.root/'one.txt').read_text(),'partial')
        self.assertEqual((self.root/'two.txt').read_text(),'accepted parent')

    def test_conflict_is_not_retried_or_lost(self):
        (self.root/'one.txt').write_text('partial');self.pause()
        (self.root/'one.txt').write_text('different accepted version');self.git('commit','-qam','parent update')
        with self.assertRaises(ValueError):git_paused.restore(self.root,'partial')
        with self.assertRaisesRegex(ValueError,'Interrupted local checkpoint'):git_paused.restore(self.root,'partial')
        pointer=json.loads((git_paused.folder(self.root,'partial')/'manifest.json').read_text())
        backup=git_paused.folder(self.root,'partial')/pointer['directory']
        self.assertEqual((backup/'0').read_text(),'partial')

    def test_collision_and_dirty_destination_are_preserved(self):
        (self.root/'new file.bin').write_text('saved');self.pause()
        (self.root/'new file.bin').write_text('human')
        with self.assertRaises(ValueError):git_paused.restore(self.root,'partial')
        self.assertEqual((self.root/'new file.bin').read_text(),'human')

    def test_failure_before_cleaning_leaves_originals(self):
        (self.root/'one.txt').write_text('partial')
        with patch.object(git_paused,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.pause()
        self.assertEqual((self.root/'one.txt').read_text(),'partial')

    def test_interrupted_cleaning_prevents_repeat(self):
        (self.root/'one.txt').write_text('partial');original=git_paused.checked
        def fail(root,*args):
            if args[0]=='restore':raise ValueError('interrupted')
            return original(root,*args)
        with patch.object(git_paused,'checked',side_effect=fail):
            with self.assertRaises(ValueError):self.pause()
        self.assertIsNone(self.pause())
        with self.assertRaisesRegex(ValueError,'Interrupted local checkpoint'):git_paused.restore(self.root,'partial')
        self.assertEqual((self.root/'one.txt').read_text(),'partial')

    def test_corrupt_backup_refuses_restore_before_touching_files(self):
        (self.root/'one.txt').write_text('partial');self.pause()
        pointer=json.loads((git_paused.folder(self.root,'partial')/'manifest.json').read_text())
        before=(self.root/'one.txt').read_bytes()
        (git_paused.folder(self.root,'partial')/pointer['directory']/'0').write_text('corrupt')
        with self.assertRaisesRegex(ValueError,'verification'):git_paused.restore(self.root,'partial')
        self.assertEqual((self.root/'one.txt').read_bytes(),before)

    def test_path_escape_is_rejected(self):
        with self.assertRaises(ValueError):git_paused.safe_path(self.root,'../outside')
        with self.assertRaises(ValueError):git_paused.safe_path(self.root,'.git/config')

    def test_restore_waits_for_active_work_and_the_saved_task(self):
        (self.root/'one.txt').write_text('partial');self.pause()
        with self.assertRaisesRegex(ValueError,'Finish active work'):
            git_paused.restore(self.root,'partial','TEST-1',allowed=False)
        with self.assertRaisesRegex(ValueError,'another task'):
            git_paused.restore(self.root,'partial','TEST-2')
        self.assertTrue(git_paused.pending(self.root,'partial'))
        git_paused.restore(self.root,'partial','TEST-1')
        self.assertEqual((self.root/'one.txt').read_text(),'partial')


if __name__=='__main__':unittest.main()
