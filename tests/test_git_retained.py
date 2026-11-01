import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import git_retained
import git_sync as git

class RetainedTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data');self.root=Path(self.temp.name)
        self.g('init','-b','main');self.g('config','user.name','Fixture');self.g('config','user.email','fixture@example.test')
        (self.root/'.gitignore').write_text('.scrum/\n');(self.root/'config.json').write_text('{"value":1}\n')
        self.g('add','.gitignore','config.json');self.g('commit','-m','Initial')
        self.base=self.g('rev-parse','HEAD');self.g('switch','-c','task/verified')
        (self.root/'config.json').write_text('{"value":2}\n');(self.root/'generated.json').write_text('{"temporary":true}\n')
    def tearDown(self):self.temp.cleanup()
    def g(self,*args):
        r=subprocess.run(['git','-C',str(self.root),*args],capture_output=True,text=True,encoding='utf-8');self.assertEqual(r.returncode,0,r.stderr);return r.stdout.strip()
    def preserve(self,planned=None):return git_retained.preserve(self.root,planned or ['config.json','generated.json'],'FIX-1','main')
    def test_exact_scope_retains_bytes_and_private_ref_without_publishing_or_deletion(self):
        before=(self.root/'config.json').read_bytes();generated=(self.root/'generated.json').read_bytes()
        r=self.preserve();self.assertTrue(r['clean']);self.assertFalse(r['published']);self.assertEqual(r['deleted_files'],0)
        self.assertEqual(git.state(self.root),('main',[]));self.assertEqual(self.g('rev-parse','main'),self.base);self.assertEqual(self.g('rev-parse','task/verified'),self.base)
        archive=self.root/r['archive'];self.assertEqual((archive/'files/config.json').read_bytes(),before);self.assertEqual((archive/'moved/generated.json').read_bytes(),generated)
        self.assertIn('"value":2',self.g('show',r['private_branch']+':config.json'))
        self.assertEqual(json.loads((archive/'manifest.json').read_text())['state'],'retained')
    def test_foreign_file_or_existing_staging_is_refused_before_any_switch(self):
        with self.assertRaisesRegex(ValueError,'explicit recovery'):self.preserve(['config.json'])
        self.g('add','config.json')
        with self.assertRaisesRegex(ValueError,'staging'):self.preserve()
        self.assertEqual(self.g('branch','--show-current'),'task/verified');self.assertTrue((self.root/'generated.json').exists())
    def test_unintegrated_source_is_refused(self):
        self.g('add','config.json');self.g('commit','-m','Unintegrated product work');(self.root/'config.json').write_text('{"value":3}\n')
        with self.assertRaisesRegex(ValueError,'include every source'):self.preserve()
        self.assertTrue((self.root/'generated.json').exists())
    def test_symlink_path_is_refused_without_following_it(self):
        with patch('git_paused.Path.is_symlink',return_value=True):
            with self.assertRaises(ValueError):self.preserve()
        self.assertTrue((self.root/'generated.json').exists())
    def test_copy_race_is_refused_and_newer_original_is_retained(self):
        import shutil
        original=shutil.copy2
        def race(src,dst):
            result=original(src,dst)
            if Path(src).name=='config.json':Path(src).write_text('{"value":99}\n')
            return result
        with patch('git_retained.shutil.copy2',side_effect=race):
            with self.assertRaisesRegex(ValueError,'changed during retention'):self.preserve()
        self.assertEqual(self.g('branch','--show-current'),'task/verified');self.assertIn('99',(self.root/'config.json').read_text())
    def test_exact_man155_scope_uses_explicit_bounded_route(self):
        planned=['config.json','generated.json']+[f'output/{i}.bin' for i in range(70)]
        for name in planned[2:]:
            path=self.root/name;path.parent.mkdir(exist_ok=True);path.write_bytes(bytes(range(256)))
        remaining=39905707-sum((self.root/name).stat().st_size for name in planned if name!='generated.json')
        with (self.root/'generated.json').open('wb') as stream:
            stream.write(b'large exact-byte fixture\x00');stream.truncate(remaining)
        before={name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in planned}
        with self.assertRaisesRegex(ValueError,'32 MiB'):self.preserve(planned)
        self.assertFalse((self.root/'.scrum/local/retained').exists())
        self.assertEqual(self.g('branch','--show-current'),'task/verified')
        r=git_retained.preserve(self.root,planned,'FIX-1','main',max_bytes=64*1024*1024)
        self.assertEqual(r['total_bytes'],39905707);self.assertTrue(r['hashes_verified'])
        self.assertEqual(r['tracked_files']+r['untracked_files'],72)
        self.assertFalse(r['published']);self.assertEqual(r['deleted_files'],0)
        archive=self.root/r['archive'];manifest=json.loads((archive/'manifest.json').read_text())
        self.assertEqual(manifest['total_bytes'],39905707);self.assertEqual(manifest['max_bytes'],64*1024*1024)
        self.assertEqual(sum(e['bytes'] for e in manifest['files'].values()),39905707)
        for name,digest in before.items():
            self.assertEqual(manifest['files'][name]['sha256'],digest)
            self.assertEqual(git_retained.signature(archive/'files'/name),digest)
            if name!='config.json':self.assertEqual(git_retained.signature(archive/'moved'/name),digest)
        self.assertEqual(git.state(self.root),('main',[]));self.assertEqual(self.g('rev-parse','main'),self.base)
        self.assertEqual(self.g('rev-parse','task/verified'),self.base)
        self.assertIn('"value":2',self.g('show',r['private_branch']+':config.json'))
    def test_supported_route_keeps_hard_cap_and_scope_staging_guards(self):
        for invalid in (0,True,128*1024*1024):
            with self.assertRaisesRegex(ValueError,'supported preservation bound'):
                git_retained.preserve(self.root,['config.json','generated.json'],'FIX-1','main',max_bytes=invalid)
        with self.assertRaisesRegex(ValueError,'explicit recovery'):
            git_retained.preserve(self.root,['config.json'],'FIX-1','main',max_bytes=64*1024*1024)
        self.g('add','config.json')
        with self.assertRaisesRegex(ValueError,'staging'):
            git_retained.preserve(self.root,['config.json','generated.json'],'FIX-1','main',max_bytes=64*1024*1024)
    def test_opt_in_bound_is_refused_before_mutation(self):
        with (self.root/'generated.json').open('wb') as stream:stream.truncate(64*1024*1024+1)
        with self.assertRaisesRegex(ValueError,'64 MiB'):
            git_retained.preserve(self.root,['config.json','generated.json'],'FIX-1','main',max_bytes=64*1024*1024)
        self.assertFalse((self.root/'.scrum/local/retained').exists())
        self.assertEqual(self.g('branch','--show-current'),'task/verified')
