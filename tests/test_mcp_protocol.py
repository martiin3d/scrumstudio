import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import diagnostics
import mcp_server

ROOT=Path(__file__).resolve().parents[1]


class McpProtocolTest(unittest.TestCase):
    def call(self,name,args):
        return mcp_server.handle({'jsonrpc':'2.0','id':27,'method':'tools/call','params':{'name':name,'arguments':args}})

    def test_advertised_nullable_estimates_and_nested_usage_are_accepted(self):
        with patch.object(mcp_server,'run',return_value={'ok':True}) as run,patch('overhead.record'):
            for value in [None,42]:
                response=self.call('task_update',{'project':1,'id':2,'estimated_tokens':value,'estimated_quota_percent':None,'usage_report':{'run_id':'x','source':'provider','input_tokens':12,'output_tokens':3}})
                self.assertNotIn('isError',response['result'],response)
            self.assertEqual(run.call_count,2)

    def test_invalid_nested_and_nonfinite_values_do_not_reach_backend(self):
        with patch.object(mcp_server,'run') as run,patch('overhead.record'):
            for args in [{'project':True,'id':2},{'project':1,'id':2,'estimated_tokens':-1},{'project':1,'id':2,'estimated_quota_percent':float('nan')},{'project':1,'id':2,'usage_report':{}},{'project':1,'id':2,'usage_report':{'run_id':'x','source':'x','input_tokens':True,'output_tokens':3}}]:
                self.assertTrue(self.call('task_update',args)['result']['isError'])
            run.assert_not_called()

    def test_malformed_requests_keep_valid_jsonrpc_responses(self):
        for value in [[],None,{'id':1,'method':[]},{'id':{},'method':'ping'}]:
            self.assertEqual(mcp_server.handle(value)['error']['code'],-32600)
        for params in [[],{'name':[]},{'name':'projects','arguments':[]}]:
            response=mcp_server.handle({'jsonrpc':'2.0','id':27,'method':'tools/call','params':params})
            self.assertEqual(response['id'],27)
            self.assertTrue(response['result']['isError'])

    def test_storage_failure_returns_safe_reference_and_survives_failed_metrics(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder,patch.object(diagnostics,'LOG_PATH',Path(folder)/'error.log'),patch.object(mcp_server,'run',side_effect=PermissionError('secret')),patch('overhead.record',side_effect=RuntimeError('metrics failed')),contextlib.redirect_stderr(io.StringIO()):
            response=self.call('project_open',{'path':'secret'})
        self.assertEqual(response['id'],27)
        error=response['result']['structuredContent']['error']
        self.assertEqual(error['code'],'STORAGE_PERMISSION_DENIED')
        self.assertNotIn('secret',json.dumps(response))
        self.assertEqual(json.loads(response['result']['content'][0]['text'])['error'],error)

    def test_committed_success_is_not_changed_by_failed_metrics(self):
        with patch.object(mcp_server,'run',return_value={'id':123}),patch('overhead.record',side_effect=RuntimeError('metrics failed')):
            response=self.call('task_create',{'project':1,'title':'x','description':'x','acceptance_criteria':['x']})
        self.assertEqual(json.loads(response['result']['content'][0]['text']),{'id':123})
