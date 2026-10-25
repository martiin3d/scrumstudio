import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock,patch
import usage_measurement as measurement
import mcp_server
from worker_catalog import EXECUTION_TOOLS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_bridge


class MeasurementTest(unittest.TestCase):
    def quota(self,at,session=10,reset=2000):
        return dict(observed_at=at,session_used_percent=session,weekly_used_percent=20,session_resets_at=reset,weekly_resets_at=9000)

    def test_baseline_retained_end_is_native_idempotent_and_quota_not_project_attribution(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            with patch('usage_measurement.time.time',return_value=1000):
                baseline=measurement.begin(folder,4,self.quota(999),{'recorded':{'runs':2}})
                with self.assertRaises(ValueError):measurement.begin(folder,4,self.quota(999),{})
            rpc=Mock();rpc.call.return_value={'audit':{'recorded':{'runs':1,'known_runs':1},'flow':{'completed_tasks':1}}}
            report=dict(run_id='one',model='fixture',tasks=[1],source='codex-terminal-turn',input_tokens=100,cached_input_tokens=90,output_tokens=5,prompt='DO_NOT_STORE')
            measurement.record_run(folder,report);measurement.record_run(folder,report)
            with patch('usage_measurement.time.time',return_value=1100):
                measurement.observe(folder,self.quota(1099,25))
                measurement.observe(folder,self.quota(1001,11))
                self.assertEqual(rpc.call.call_count,0)
                result=measurement.finish(folder,'Quota boundary',rpc,4)
                self.assertIsNone(measurement.finish(folder,'duplicate',rpc,4))
            self.assertEqual(rpc.call.call_count,1)
            self.assertEqual(result['account_delta_percentage_points']['session'],15)
            self.assertEqual(result['recorded_runs'],1)
            self.assertIn('Shared provider account',result['quota_scope'])
            self.assertEqual(result['baseline_usage'],baseline['baseline_usage'])
            with patch('usage_measurement.time.time',return_value=99999):
                self.assertEqual(measurement.summary(folder)['account_delta_percentage_points']['session'],15)
            saved=Path(folder)/result['report'];self.assertTrue(saved.is_file())
            self.assertNotIn('DO_NOT_STORE',saved.read_text())

    def test_reset_stale_or_missing_telemetry_never_fakes_a_delta(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            with patch('usage_measurement.time.time',return_value=1000):
                with self.assertRaises(ValueError):measurement.begin(folder,4,{}, {})
                measurement.begin(folder,4,self.quota(999),{})
            with patch('usage_measurement.time.time',return_value=1200):
                measurement.observe(folder,self.quota(1199,2,reset=3000))
                self.assertIsNone(measurement.summary(folder)['account_delta_percentage_points']['session'])
            with patch('usage_measurement.time.time',return_value=1600):
                self.assertIsNone(measurement.summary(folder)['account_delta_percentage_points']['weekly'])
                rpc=Mock();rpc.call.side_effect=RuntimeError('metrics temporarily unavailable')
                result=measurement.finish(folder,'Stop',rpc,4)
                self.assertIn('temporarily unavailable',result['snapshot_error'])
                self.assertIsNone(result['recorded_interval'])

    def test_other_provider_cannot_replace_or_end_codex_quota_measurement(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            with patch('usage_measurement.time.time',return_value=1000):
                measurement.begin(folder,4,self.quota(999),{})
                measurement.observe(folder,self.quota(1000,80),client='claude')
                self.assertEqual(measurement.summary(folder)['latest_usage']['session_used_percent'],10)
                rpc=Mock()
                self.assertIsNone(measurement.finish(folder,'Other provider quota',rpc,4,client='claude'))
                rpc.call.assert_not_called()
                self.assertEqual(measurement.summary(folder)['status'],'recording')

    def test_no_measurement_means_no_extra_rpc(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            rpc=Mock();self.assertIsNone(measurement.finish(folder,'idle',rpc,4));rpc.call.assert_not_called()

    def test_execution_schema_hides_startup_admin_and_keeps_rare_fallback_available(self):
        with patch('sys.argv',['mcp_server.py','--worker-schema']):old=mcp_server.catalog()
        with patch('sys.argv',['mcp_server.py','--worker-schema','--execution-schema']):new=mcp_server.catalog()
        self.assertEqual({t['name'] for t in new},set(EXECUTION_TOOLS))
        self.assertEqual(len(new),9)
        self.assertLess(len(json.dumps(new)),len(json.dumps(old)))
        self.assertIn('work_start',{name for name,_,_ in mcp_server.TOOLS})
        self.assertNotIn('project_control',{t['name'] for t in new})

    def test_batch_prompt_has_no_single_task_exit_or_duplicate_claim_order(self):
        source='Claim an eligible task/files (claimed=true) ONLY for T-1. Complete all acceptance, verify, work_finish(close_session=true), exit; the supervisor will continue with the next eligible task.'
        prompt=agent_bridge.batch_prompt(source,[{'id':1},{'id':2}],'codex','T-1')
        self.assertNotIn('close_session=true',prompt)
        self.assertNotIn('Claim an eligible',prompt)
        self.assertIn('claim_next=true',prompt)
        self.assertIn('Others remain TODO',prompt)
