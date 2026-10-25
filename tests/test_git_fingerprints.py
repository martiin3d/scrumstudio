"""Native bulk fingerprints and metadata repair preserve real work."""
import os
import subprocess
import unittest
from unittest.mock import patch

import git_completion
import git_index
import git_sync
import test_git_native_recovery as native


class GitFingerprintTest(unittest.TestCase):
    def setUp(self):
        native.NativeGitRecoveryTest.setUp(self)
        self.git('config','core.autocrlf','false')
        (self.root/'.gitattributes').write_text('a.txt text eol=lf\nb.txt text eol=lf\n')
        for name in ('a.txt','b.txt'):(self.root/name).write_bytes(b'base\n')
        self.git('add','--renormalize','.')
        self.git('add','.gitattributes')
        self.git('commit','--allow-empty','-qm','deterministic line endings')
    git=native.NativeGitRecoveryTest.git
    def test_bulk_hashes_match_path_filters_and_use_one_process(self):
        self.git('config','core.autocrlf','false')
        (self.root/'.gitattributes').write_text('*.txt text eol=lf\n')
        names=[f'folder/file {i}[x].txt' for i in range(205)]+['folder/caf\u00e9.txt']
        if os.name!='nt':names.append('folder/quote".txt')
        (self.root/'folder').mkdir()
        for name in names:(self.root/name).write_bytes(b'one\r\ntwo\r\n')
        self.git('add','.');self.git('commit','-qm','many files')
        expected_hash=self.git('hash-object','--path='+names[0],'--',names[0])
        expected={name:expected_hash for name in names}
        raw=git_sync._git
        with patch.object(git_sync,'_git',wraps=raw) as calls:
            self.assertEqual(git_completion.file_blobs(self.root,names),expected)
        hashes=[c for c in calls.call_args_list if c.args[1]=='hash-object']
        self.assertEqual(len(hashes),1)
        self.assertEqual(hashes[0].kwargs['timeout'],30)
        with patch.object(git_sync,'_git',wraps=raw) as calls:
            self.assertTrue(git_completion.matches_commit(self.root,expected,'HEAD'))
        self.assertLess(len(calls.call_args_list),5)
        missing=names.pop();(self.root/missing).unlink()
        blobs=git_completion.file_blobs(self.root,names+[missing])
        self.assertIsNone(blobs[missing])
        self.assertFalse(git_completion.matches_commit(self.root,blobs,'HEAD'))

    def test_failed_bulk_hash_cannot_become_a_receipt(self):
        raw=git_sync._git
        def fail(root,*args,**kwargs):
            if args[0]=='hash-object':return subprocess.CompletedProcess(args,1,'','filter failed')
            return raw(root,*args,**kwargs)
        with patch.object(git_sync,'_git',side_effect=fail):
            with self.assertRaises(ValueError):git_completion.file_blobs(self.root,['a.txt'])

    def test_index_metadata_repair_preserves_filtered_bytes_and_index_blobs(self):
        (self.root/'.gitattributes').write_text('*.txt text eol=lf\n')
        self.git('add','.gitattributes');self.git('commit','-qm','attributes')
        (self.root/'a.txt').write_bytes(b'base\r\n')
        before=self.git('ls-files','--stage','-z')
        raw=(self.root/'a.txt').read_bytes()
        self.assertTrue(git_index.refresh_identical(self.root))
        self.assertEqual(self.git('ls-files','--stage','-z'),before)
        self.assertEqual((self.root/'a.txt').read_bytes(),raw)
        self.assertEqual(self.git('status','--porcelain'),'')

    def test_index_repair_refuses_real_content_changes(self):
        (self.root/'a.txt').write_text('real edit\n')
        before=(self.root/'.git/index').read_bytes()
        self.assertFalse(git_index.refresh_identical(self.root))
        self.assertEqual((self.root/'.git/index').read_bytes(),before)
        self.assertEqual((self.root/'a.txt').read_text(),'real edit\n')

    def test_index_repair_respects_foreign_staging_and_locks(self):
        (self.root/'a.txt').write_text('staged\n');self.git('add','a.txt')
        before=(self.root/'.git/index').read_bytes()
        self.assertFalse(git_index.refresh_identical(self.root))
        self.assertEqual((self.root/'.git/index').read_bytes(),before)
        lock=self.root/'.git/index.lock';lock.write_text('another process')
        self.assertFalse(git_index.refresh_identical(self.root))
        self.assertEqual(lock.read_text(),'another process')
        lock.unlink()

    def test_installed_index_does_not_remove_a_new_foreign_lock(self):
        (self.root/'.gitattributes').write_text('*.txt text eol=lf\n')
        self.git('add','.gitattributes');self.git('commit','-qm','attributes')
        (self.root/'a.txt').write_bytes(b'base\r\n')
        lock=self.root/'.git/index.lock';replace=os.replace
        def handed_off(source,destination):
            replace(source,destination)
            lock.write_text('next Git process')
        with patch.object(git_index.os,'replace',side_effect=handed_off):
            self.assertTrue(git_index.refresh_identical(self.root))
        self.assertEqual(lock.read_text(),'next Git process')
        lock.unlink()

    def test_private_index_rejects_concurrent_content_change(self):
        (self.root/'.gitattributes').write_text('*.txt text eol=lf\n')
        self.git('add','.gitattributes');self.git('commit','-qm','attributes')
        (self.root/'a.txt').write_bytes(b'base\r\n')
        before=(self.root/'.git/index').read_bytes();raw=git_sync._git
        def racing(root,*args,**kwargs):
            if args[0]=='add' and kwargs.get('index_file'):(self.root/'a.txt').write_text('concurrent edit\n')
            return raw(root,*args,**kwargs)
        with patch.object(git_sync,'_git',side_effect=racing):
            self.assertFalse(git_index.refresh_identical(self.root))
        self.assertEqual((self.root/'.git/index').read_bytes(),before)
        self.assertEqual((self.root/'a.txt').read_text(),'concurrent edit\n')


if __name__=='__main__':unittest.main()
