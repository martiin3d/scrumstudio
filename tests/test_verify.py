import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('scrum_verify',Path(__file__).resolve().parents[1]/'scripts/verify.py')
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)

class VerificationCacheTest(unittest.TestCase):
    def test_changes_additions_and_deletions_invalidate_but_local_logs_do_not(self):
        with tempfile.TemporaryDirectory(dir=verify.ROOT/'data') as folder:
            root=Path(folder);source=root/'feature.py';source.write_text('first')
            first=verify.fingerprint(root)
            (root/'data').mkdir();(root/'data/log').write_text('local output')
            self.assertEqual(first,verify.fingerprint(root))
            source.write_text('changed');self.assertNotEqual(first,verify.fingerprint(root))
            source.write_text('first');(root/'new.json').write_text('{}');self.assertNotEqual(first,verify.fingerprint(root))
            (root/'new.json').unlink();self.assertEqual(first,verify.fingerprint(root))
            source.unlink();self.assertNotEqual(first,verify.fingerprint(root))

    def test_only_matching_success_is_reusable(self):
        with tempfile.TemporaryDirectory(dir=verify.ROOT/'data') as folder:
            path=Path(folder)/'pass.json'
            verify.save_pass(path,'signature','tree')
            self.assertTrue(verify.read_cached(path,'signature')['passed'])
            self.assertIsNone(verify.read_cached(path,'other'))
            value=json.loads(path.read_text());value['passed']=False;path.write_text(json.dumps(value))
            self.assertIsNone(verify.read_cached(path,'signature'))
            path.write_text('partial');self.assertIsNone(verify.read_cached(path,'signature'))
        check={'tests':['test_feature.py'],'node_syntax':False}
        self.assertNotEqual(verify.signature(check,'one'),verify.signature(check,'two'))
        self.assertNotEqual(verify.signature(check,'one'),verify.signature(check|{'tests':['test_other.py']},'one'))
