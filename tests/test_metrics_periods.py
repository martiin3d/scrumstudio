import time
import unittest
from unittest.mock import patch
import test_app
import metrics_periods
import overhead


class PeriodTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def test_observed_quota_window_and_wakeup_campaigns(self):
        now=time.time()
        with patch('metrics_periods.time.time',return_value=now):
            metrics_periods.capture(self.p,'agent_checkpoint',{'client':'codex','session_used_percent':10,'session_resets_at':now+1000,'observed_at':now},{'control':{'generation':1,'changed':now-500,'paused':0}})
            metrics_periods.capture(self.p,'agent_checkpoint',{'client':'codex'}, {'control':{'generation':1,'changed':now-500,'paused':0}})
            metrics_periods.capture(self.p,'project_control',{}, {'control':{'generation':3,'changed':now-100,'paused':0}})
        rows=metrics_periods.catalog(self.p)
        quota=[r for r in rows if r['kind']=='quota']
        self.assertEqual(len(quota),1)
        self.assertEqual(quota[0]['ended']-quota[0]['started'],18000)
        old=next(r for r in rows if r['id']=='wakeup:1')
        self.assertEqual(old['ended'],now-100)
        self.assertEqual(metrics_periods.resolve(self.p,'wakeup:1',at=now)['end_at'],now-100)

    def test_stale_or_missing_provider_data_does_not_invent_windows(self):
        now=time.time()
        metrics_periods.capture(self.p,'agent_checkpoint',{'client':'codex','session_used_percent':10,'session_resets_at':now+1000,'observed_at':now-1000},{})
        self.assertFalse(metrics_periods.catalog(self.p))
        with self.assertRaises(ValueError):metrics_periods.resolve(self.p,'unknown')

    def test_timed_calls_keep_run_end_without_recording_contents(self):
        key=overhead.start_session(self.p,self.actor,'Light')
        overhead.record(self.p,'task_get',10,20,3,False,session=key,task=4)
        row=next(r for r in metrics_periods.catalog(self.p) if r['id']==key)
        self.assertGreaterEqual(row['ended'],row['started'])
