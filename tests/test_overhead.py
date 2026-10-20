import json
import unittest
import test_app
from store import run
from mcp_server import handle
import overhead
import time
from unittest.mock import patch
import test_storage
import storage


class OverheadTest(unittest.TestCase):
    setUp = test_app.WorkflowTest.setUp
    tearDown = test_app.WorkflowTest.tearDown

    def test_counts_bytes_errors_without_contents(self):
        for name, args in [('task_get', {'project': self.p, 'id': 999}), ('next_work', {'project': self.p})]:
            handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
        result = run('coordination_metrics', {'project':self.p})
        self.assertEqual(result['totals']['calls'], 2)
        self.assertEqual(result['totals']['errors'], 1)
        self.assertGreater(result['totals']['response_bytes'], 0)
        self.assertIsNone(result['tokens'])
        self.assertNotIn('arguments', json.dumps(result))

    def test_explicit_usage_wins(self):
        a = {'project':self.p,'actor':self.actor,'client':'codex','session_used_percent':12,'weekly_used_percent':3}
        self.assertEqual(overhead.cached_usage(a), a)


class CachedUsageTest(unittest.TestCase):
    setUp = test_storage.StorageTest.setUp
    tearDown = test_storage.StorageTest.tearDown
    project = test_storage.StorageTest.project

    def test_cache_preserves_timestamp_and_enforces_threshold(self):
        project, folder = self.project()
        target = overhead.usage_path(project, self.agent, 'codex')
        storage.atomic_json(target, {'session_used_percent':93,'weekly_used_percent':3,'observed_at':time.time()})
        result = run('agent_checkpoint', {'project':project,'actor':self.agent,'client':'codex'})
        self.assertTrue(result['should_stop'])
        storage.atomic_json(target, {'session_used_percent':1,'weekly_used_percent':3,'observed_at':time.time()-1000})
        result = run('agent_checkpoint', {'project':project,'actor':self.agent,'client':'codex'})
        self.assertTrue(result['should_stop'])
        self.assertIn('stale', result['reason'])

    def test_claude_reads_documented_status_line_feed(self):
        project, folder = self.project()
        target = overhead.usage_path(project, self.agent, 'claude').with_name('claude-usage.json')
        storage.atomic_json(target, {'session_used_percent':10,'weekly_used_percent':3,'observed_at':time.time()})
        result = run('agent_checkpoint', {'project':project,'actor':self.agent,'client':'claude'})
        self.assertFalse(result['should_stop'], result)

    def test_measurement_failure_does_not_break_delivery(self):
        with patch('overhead.connection', side_effect=OSError('disk unavailable')):
            overhead.record(1,'task_get',10,10,1,False)
