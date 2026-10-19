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
spec=importlib.util.spec_from_file_location('scrum_mcp_cli',ROOT/'scripts/mcp_client.py')
cli=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class McpClientTest(unittest.TestCase):
    def invoke(self,arguments='{}',error=None):
        out,err=io.StringIO(),io.StringIO()
        with patch.object(cli,'ScrumClient') as factory,patch.object(sys,'stdin',io.StringIO(arguments)),contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            factory.return_value.call.return_value={'claimed':False,'reason':'conflict'}
            factory.return_value.call.side_effect=error
            code=cli.main(['--tool','task_claim','--timeout','0.5'])
        return code,out.getvalue(),err.getvalue(),factory

    def test_success_preserves_conflict_and_forwards_timeout(self):
        code,out,err,factory=self.invoke('{"project":7}')
        self.assertEqual(code,0)
        self.assertFalse(json.loads(out)['claimed'])
        factory.assert_called_once_with(timeout=.5)
        factory.return_value.call.assert_called_once_with('task_claim',{'project':7},timeout=.5)
        factory.return_value.close.assert_called_once()
        self.assertEqual(json.loads(err)['transport'],'stdio')

    def test_failures_never_retry_and_warn_about_uncertain_writes(self):
        for message in ['Storage error; check local server logs.','Local JSON-RPC response timed out']:
            with self.subTest(message=message):
                code,out,err,factory=self.invoke(error=RuntimeError(message))
                self.assertEqual(code,1)
                self.assertEqual(out,'')
                factory.return_value.call.assert_called_once()
                factory.return_value.close.assert_called_once()
                failure=json.loads(err.splitlines()[0])
                self.assertEqual(failure['error'],message)
                self.assertFalse(failure['retried'])
                self.assertIn('already have committed',failure['next_step'])

    def test_invalid_arguments_do_not_start_server(self):
        for arguments in ['[]','broken']:
            code,_,_,factory=self.invoke(arguments)
            self.assertEqual(code,2)
            factory.assert_not_called()

    def test_structured_storage_error_survives_cli(self):
        detail={'code':'STORAGE_PERMISSION_DENIED','message':'No access','diagnostic_id':'ref','next_step':'Request access'}
        error=cli.ToolError({'structuredContent':{'error':detail},'content':[]})
        code,out,err,_=self.invoke(error=error)
        self.assertEqual(code,1)
        self.assertEqual(json.loads(err.splitlines()[0])['diagnostic'],detail)

    def test_json_client_ignores_noise_and_closes_streams(self):
        script="import sys,json; msg=json.loads(sys.stdin.readline()); print('noise'); print('[]'); print(json.dumps({'id':msg['id'],'result':{'ok':True}})); sys.stdout.flush()"
        client=cli.JsonClient([sys.executable,'-c',script])
        try:self.assertEqual(client.rpc('ping',timeout=2),{'ok':True})
        finally:client.close()
        self.assertTrue(client.process.stdin.closed)
        self.assertTrue(client.process.stdout.closed)

    def test_json_client_timeout_is_bounded(self):
        script="import sys; sys.stdin.readline(); sys.stdin.read()"
        client=cli.JsonClient([sys.executable,'-c',script])
        try:
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                client.rpc('ping',timeout=.05)
        finally:client.close()

    def test_malformed_tool_response_is_reported(self):
        client=cli.ScrumClient.__new__(cli.ScrumClient)
        for result in [[],{}, {'content':[]}]:
            with patch.object(client,'rpc',return_value=result),self.assertRaisesRegex(RuntimeError,'Malformed'):
                client.call('projects',{})

    def test_real_stdio_from_other_directory_and_unicode_args_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder:
            path=Path(folder)
            arguments=path/'args.json'
            arguments.write_text('{}',encoding='utf-8-sig')
            env=dict(os.environ,SCRUM_REGISTRY=str(path/'registry.db'))
            env.pop('SCRUM_DB',None)
            process=subprocess.run([sys.executable,str(ROOT/'scripts/mcp_client.py'),'--tool','projects','--args-file',str(arguments)],cwd=folder,env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(json.loads(process.stdout),[])
            self.assertEqual(json.loads(process.stderr)['transport'],'stdio')


if __name__=='__main__':
    unittest.main()
