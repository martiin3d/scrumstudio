import unittest
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from scripts.eval_worker import CASES, capture_layer, compare


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

    def test_behavioral_comparison_checks_manifests_and_reports_single_case(self):
        fixed={'launch':b'fixed workflow', 'verification':b'exact check'}
        before, after = trials()[:2], trials()[:2]
        for variant, layer in ((before,b'long rule'), (after,b'short rule')):
            for row in variant:
                row['layer']=capture_layer('rule','scope','native-cli',fixed,layer)
                row['seconds']=3.5
                row['tool_actions']=3
        after[0]['usage']['input_tokens']=85
        after[1]['usage']['input_tokens']=80
        report=compare(before,after,behavioral=True)
        self.assertEqual(report['recommendation']['action'],'keep_candidate')
        self.assertEqual(report['comparisons'][0]['candidate_minus_baseline']['seconds'],0)
        self.assertEqual(compare(before[:1],after[:1],behavioral=True)['recommendation']['action'],'collect_more')
        after[0]['layer']=capture_layer('rule','scope','native-cli',fixed | {'verification':b'changed check'},b'short rule')
        with self.assertRaisesRegex(ValueError,'other inputs fixed'):
            compare(before,after,behavioral=True)

    def test_behavioral_unknown_costs_and_failed_acceptance_remain_explicit(self):
        fixed={'launch':b'fixed', 'verification':b'fixed'}
        before, after = trials()[:1], trials()[:1]
        before[0]['layer']=capture_layer('skill','playwright','native-cli',fixed,None)
        after[0]['layer']=capture_layer('skill','playwright','native-cli',fixed,b'loaded skill bytes')
        after[0]['usage']={}
        report=compare(before,after,behavioral=True)
        self.assertIsNone(report['comparisons'][0]['candidate_minus_baseline']['uncached_input_tokens'])
        after[0]['checks']['verified_finish']=False
        report=compare(before,after,behavioral=True)
        self.assertIsNone(report['comparisons'][0]['candidate_minus_baseline'])
        self.assertEqual(report['recommendation']['action'],'keep_baseline')
