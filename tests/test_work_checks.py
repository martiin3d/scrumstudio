import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from scripts import work_checks
from scripts import verify
from unittest.mock import patch


class WorkChecksTest(unittest.TestCase):
    def test_risk_drift_keeps_the_task_base_and_pending_checks_across_verification(self):
        from context_tools import verification_risk_state, workspace_delta
        from scrum_cli import atomic

        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            context=root/'.scrum/local/claim.json'
            atomic(context,{'schema':1,'current':{'id':9}})
            def git(*args):
                return subprocess.run(['git','-C',folder,*args],capture_output=True,text=True,check=True).stdout.strip()
            git('init','-q')
            git('config','user.name','Verification fixture')
            git('config','user.email','fixture@example.invalid')
            source=root/'context_tools.py'
            source.write_text('value = 1\n',encoding='utf-8')
            git('add','context_tools.py')
            git('commit','-qm','Initial')
            with patch.object(verify,'ROOT',root):
                initial,_=verify.claim_risk_plan(['context_tools.py'],None,str(context))
                self.assertNotIn('risk_change',initial)
                source.write_text('value = 1\nshould_stop = True\n',encoding='utf-8')
                git('commit','-qam','Add a policy dependency')
                changed,checkpoint=verify.claim_risk_plan(['context_tools.py'],None,str(context))
                self.assertEqual(changed['diff_base'],initial['diff_base'])
                self.assertEqual([item['area'] for item in changed['risk_change']['introduced']],['permissions'])
                self.assertIn('test_policy.py',changed['risk_change']['pending'][0]['checks'])
                repeated,checkpoint=verify.claim_risk_plan(['context_tools.py'],None,str(context))
                self.assertEqual(repeated['risk_change']['introduced'],[])
                self.assertTrue(workspace_delta(root,root,{'id':9})['verification_risk']['pending'])
                verify.record_verified_risk(checkpoint,repeated)
                self.assertEqual(verification_risk_state(root,9)['pending'],[])
                self.assertIsNone(workspace_delta(root,root,{'id':9}))

    def test_risk_drift_does_not_reannounce_unrelated_or_unavailable_paths(self):
        hint={'area':'schema','paths':['store.py'],'observed_paths':['store.py'],
              'reason':'Schema changed','checks':['test_storage.py']}
        state,added=verify.risk_state({}, {'diff_base':'baseline','diff_covered':['store.py'],
                                         'diff_unavailable':[],'risk':[hint]})
        self.assertEqual(added[0]['path'],'store.py')
        state,added=verify.risk_state(state,{'diff_base':'baseline','diff_covered':['public/app.js'],
                                            'diff_unavailable':['store.py'],'risk':[]})
        self.assertEqual(added,[])
        self.assertEqual(state['unknown'],['store.py'])
        self.assertEqual(state['pending'][0]['path'],'store.py')

    def test_batch_hashes_report_mismatch_missing_and_scope(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);(root/'a').write_bytes(b'checked')
            good=hashlib.sha256(b'checked').hexdigest()
            result=work_checks.files(root,[{'path':'a','sha256':good},{'path':'missing'}])
            self.assertFalse(result['passed']);self.assertTrue(result['items'][0]['passed'])
            self.assertEqual(result['expected_hashes'],1)
            self.assertFalse(work_checks.files(root,[{'path':'a','sha256':'0'*64}])['passed'])
            with self.assertRaises(ValueError):work_checks.files(root,[{'path':'../escaped'}])
            with self.assertRaises(ValueError):work_checks.files(root,[{'path':'a'},{'path':'a'}])

    def test_visual_samples_cover_variants_mandatory_and_risks_without_false_pass(self):
        cases=[dict(id=str(n),family='button',variant='rose') for n in range(20)]
        cases.extend([dict(id='required',family='button',variant='rose',required=True),dict(id='risk',family='button',variant='rose',changed_risk=True),dict(id='cream',family='button',variant='cream')])
        result=work_checks.visual(cases,samples=1)
        self.assertEqual({r['id'] for r in result['selected']},{'required','risk','cream'})
        self.assertTrue(result['target_exceeded']);self.assertNotIn('passed',result)
        self.assertEqual(len(work_checks.visual(cases,all_cases=True)['selected']),len(cases))

    def test_cli_keeps_hundreds_of_successes_out_of_stdout(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);rows=[]
            for n in range(205):(root/str(n)).write_text('ok');rows.append({'path':str(n)})
            manifest=root/'input.json';manifest.write_text(json.dumps(rows));report=root/'report.json'
            result=subprocess.run([sys.executable,str(Path(work_checks.__file__)),'files','--root',str(root),'--input',str(manifest),'--report',str(report)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            brief=json.loads(result.stdout);self.assertEqual(brief['files'],205);self.assertEqual(brief['failures'],[])
            self.assertNotIn('items',brief);self.assertLess(len(result.stdout),1000)
            self.assertEqual(len(json.loads(report.read_text())['items']),205)
