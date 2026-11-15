import unittest
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from scripts.eval_worker import CASES, compare


def trials():
    return [dict(case=name, replicate=1, client='codex', model='fixture-model', fixture_sha256='a'*64,
                 checks=dict.fromkeys(case['checks'], True), calls=['show_same_scope', 'finish'],
                 usage=dict(input_tokens=100, cached_input_tokens=40, output_tokens=10))
            for name, case in CASES.items()]


class WorkerEvaluationTest(unittest.TestCase):
    def test_prepare_and_collect_from_native_fixture(self):
        from scripts.eval_worker import collect_fixture, prepare_fixture
        from store import run
        import overhead

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder,patch.dict(os.environ):
            root=Path(folder)
            prepared=prepare_fixture(root,'new-claim')
            project,task=prepared['project'],prepared['task']
            actor=run('users',{})[0]['id']
            session=overhead.start_session(project,actor,'Light')
            overhead.record_startup(project,actor,'fixture-run','codex','fixture-model',session,[task],512,{})
            overhead.record(project,'task_get',10,20,1,False,session,task,actor,channel='cli')
            (root/'feature.py').write_text('def first():\n    return 42\n',encoding='utf-8')
            run('task_update',dict(project=project,id=task,actor=actor,status='Done',evidence='42 observed'))
            item=collect_fixture(root,'fixture-run',1,{'verified_redundant_calls':['show_same_scope']})
            self.assertTrue(all(item['checks'].values()))
            self.assertEqual(item['calls'],['show_same_scope'])
            self.assertIsNone(item['usage']['input_tokens'])
    def test_matched_trials_measure_savings_without_guessing_missing_usage(self):
        before, after = trials(), trials()
        for row in after:
            row['calls'] = ['finish']
            row['usage'] = dict(input_tokens=80, output_tokens=8)
        report = compare(before, after)
        self.assertEqual(report['matched_successes'], 3)
        for result in report['comparisons']:
            delta = result['candidate_minus_baseline']
            self.assertEqual(delta['redundant_calls'], -1)
            self.assertEqual(delta['input_tokens'], -20)
            self.assertIsNone(delta['uncached_input_tokens'])

    def test_regressions_do_not_participate_in_savings(self):
        before, after = trials(), trials()
        after[0]['checks']['verified_finish'] = False
        report = compare(before, after)
        self.assertEqual(report['quality_regressions'], 1)
        result = next(row for row in report['comparisons'] if row['quality_regression'])
        self.assertIsNone(result['candidate_minus_baseline'])
        after[0]['model'] = 'different'
        with self.assertRaisesRegex(ValueError, 'mismatched model'):
            compare(before, after)
