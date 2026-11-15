import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('scrum_verify',Path(__file__).resolve().parents[1]/'scripts/verify.py')
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)

class VerificationCacheTest(unittest.TestCase):
    def test_diff_risk_selects_targeted_checks_and_explains_each_path(self):
        changes = {
            'workflow.py': ['db.execute("ALTER TABLE tasks ADD COLUMN new_field TEXT")'],
            'scripts/agent_bridge.py': ['approval_policy = "on-request"'],
            'context_tools.py': ['return publish_verified(root, remote, refspec)'],
            'docs/example.md': ['DROP TABLE'],
        }
        with patch.object(verify,'diff_lines',side_effect=lambda name,base:changes[name]):
            result=verify.plan(list(changes),'origin/main')
        self.assertEqual({item['area']:item['paths'] for item in result['risk']},
                         {'schema':['workflow.py'],'permissions':['scripts/agent_bridge.py'],'git_delivery':['context_tools.py']})
        self.assertTrue({'test_storage.py','test_policy.py','test_git_completion_recovery.py','test_workflow.py'}.issubset(result['tests']))
        self.assertEqual(result['diff_base'],'origin/main')

    def test_git_diff_reports_deleted_sensitive_lines_and_untracked_source(self):
        with tempfile.TemporaryDirectory(dir=verify.ROOT/'data') as folder:
            root=Path(folder)
            def git(*args):
                subprocess.run(['git','-C',folder,*args],capture_output=True,check=True)
            git('init','-q')
            source=root/'schema.py'
            source.write_text('CREATE TABLE old\n',encoding='utf-8')
            git('add','schema.py')
            git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','base')
            source.unlink()
            (root/'new.py').write_text('approval_policy = None\n',encoding='utf-8')
            self.assertEqual(verify.diff_lines('schema.py','HEAD',root),['CREATE TABLE old'])
            self.assertEqual(verify.diff_lines('new.py','HEAD',root),['approval_policy = None'])
            self.assertIsNone(verify.diff_lines('new.py','nonexistent-ref',root))

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
