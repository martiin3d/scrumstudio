import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from scripts import work_checks


class WorkChecksTest(unittest.TestCase):
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
