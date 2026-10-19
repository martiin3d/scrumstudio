import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('scrum_check_mcp',ROOT/'scripts/check_mcp.py')
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class CheckMcpTest(unittest.TestCase):
    def test_real_probe_is_bounded_and_does_not_expose_project_contents(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder:
            env=dict(os.environ,SCRUM_REGISTRY=str(Path(folder)/'registry.db'))
            env.pop('SCRUM_DB',None)
            result=subprocess.run([sys.executable,str(ROOT/'scripts/check_mcp.py'),'--project-path',folder],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        body=json.loads(result.stdout)
        self.assertEqual(body['handshake'],'ok')
        self.assertFalse(body['project_registered'])
        self.assertFalse(body['writes_tested'])
        self.assertNotIn('projects',body)
        self.assertGreater(body['elapsed_ms'],0)

    def test_structured_project_error_keeps_failure_phase_and_reference(self):
        detail={'code':'STORAGE_BUSY','message':'Busy','diagnostic_id':'ref'}
        with patch.object(probe,'JsonClient') as factory,contextlib.redirect_stdout(io.StringIO()) as stdout:
            client=factory.return_value
            client.rpc.side_effect=[{'serverInfo':{'name':'scrum','version':'x'}},{'tools':[]},{'isError':True,'structuredContent':{'error':detail}}]
            code=probe.main([])
        self.assertEqual(code,1)
        body=json.loads(stdout.getvalue())
        self.assertEqual(body['phase'],'projects')
        self.assertEqual(body['diagnostic'],detail)
        client.close.assert_called_once()
        self.assertEqual(client.rpc.call_count,3)

    def test_timeout_is_reported_without_retry(self):
        with patch.object(probe,'JsonClient') as factory,contextlib.redirect_stdout(io.StringIO()) as stdout:
            factory.return_value.rpc.side_effect=RuntimeError('Local JSON-RPC response timed out')
            code=probe.main(['--timeout','.05'])
        self.assertEqual(code,1)
        result=json.loads(stdout.getvalue())
        self.assertEqual(result['phase'],'initialize')
        self.assertFalse(result['retried'])
        factory.return_value.rpc.assert_called_once()
        factory.return_value.close.assert_called_once()
