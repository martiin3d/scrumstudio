import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import codex_interactive as client
import agent_bridge


class InteractiveTest(unittest.TestCase):
    def test_background_sessions_are_temporary_in_all_permission_modes_and_outcomes(self):
        # No provider/model call: exercise the actual adapter with a native
        # protocol fixture, including failed turns and both review modes.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            server=Path(folder)/'temporary_worker.py'
            server.write_text('''import json,sys
for line in sys.stdin:
 request=json.loads(line);method=request.get('method')
 if method=='initialize':print(json.dumps({'id':request['id'],'result':{}}),flush=True)
 elif method=='thread/start':
  params=request['params']
  assert params['ephemeral'] is True
  assert params['approvalPolicy']==sys.argv[1]
  assert params['approvalsReviewer']==sys.argv[2]
  print(json.dumps({'id':request['id'],'result':{'thread':{'id':'temporary','ephemeral':True}}}),flush=True)
 elif method=='turn/start':
  print(json.dumps({'method':'turn/completed','params':{'threadId':'temporary','turn':{'status':sys.argv[3]}}}),flush=True)
  break
''',encoding='utf-8')
            for full_access,reviewer in [(False,'auto_review'),(False,'user'),(True,'auto_review')]:
                for status in ('completed','failed'):
                    with self.subTest(full_access=full_access,reviewer=reviewer,status=status),contextlib.redirect_stdout(io.StringIO()):
                        result=client.run([sys.executable,str(server),'never' if full_access else 'on-request',reviewer,status],1,9,folder,'task',rpc=Mock(),reviewer=reviewer,full_access=full_access)
                        self.assertEqual(result,0 if status=='completed' else 1)

    def test_full_access_is_explicit_and_reaches_native_configuration(self):
        cmd=['codex','-c','approval_policy="on-request"','-c','default_permissions="scrum-worker"','exec','--json']
        result=agent_bridge.interactive_command(cmd,'codex',4,9,Path.cwd(),'model',{'agent_approval_mode':'Full access'})
        self.assertIn('--full-access',result)
        self.assertIn('approval_policy="never"',result)
        self.assertIn('default_permissions=":danger-full-access"',result)
        self.assertNotIn('approval_policy="on-request"',result)

    def test_per_project_review_mode_preserves_sandbox_and_never_bypasses(self):
        cmd=['codex','-c','approvals_reviewer="auto_review"','-c','default_permissions="scrum-worker"','exec','--json']
        for mode,reviewer in [('Ask in chat','user'),('Auto review','auto_review')]:
            result=agent_bridge.interactive_command(cmd,'codex',4,9,Path.cwd(),'model',{'agent_approval_mode':mode})
            self.assertIn('approvals_reviewer='+json.dumps(reviewer),result)
            self.assertIn('default_permissions="scrum-worker"',result)
            self.assertEqual(result[-1],'app-server')
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',result)

    def test_only_requested_permissions_are_returned_and_no_permanent_cache(self):
        requested={'permissions':{'fileSystem':{'write':['C:/exact/cache']}}}
        self.assertEqual(client.approval_result('item/permissions/requestApproval',requested,['Approve once']),{'permissions':requested['permissions'],'scope':'turn'})
        self.assertEqual(client.approval_result('item/permissions/requestApproval',requested,['Deny'])['permissions'],{})
        self.assertEqual(client.approval_result('item/commandExecution/requestApproval',{},['Approve once']),{'decision':'accept'})

    def test_secret_or_truncated_requests_fail_closed(self):
        with self.assertRaises(ValueError):client.request_questions('item/tool/requestUserInput',{'questions':[{'isSecret':True}]})
        with self.assertRaises(ValueError):client.request_questions('item/commandExecution/requestApproval',{'command':'x'*12000})
        with self.assertRaises(ValueError):client.request_questions('unknown/request',{})

    def test_native_callback_round_trip_and_usage_without_model_call(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            server=Path(folder)/'fake.py'
            server.write_text('''import sys,json
def emit(x):print(json.dumps(x),flush=True)
for line in sys.stdin:
 x=json.loads(line);m=x.get('method');i=x.get('id')
 if m=='initialize':emit({'id':i,'result':{}})
 elif m=='thread/start':
  assert x['params']['approvalPolicy']=='never'
  assert x['params']['ephemeral'] is True
  emit({'id':i,'result':{'thread':{'id':'fixture'},'model':'fixture-model'}})
 elif m=='turn/start':
  emit({'id':i,'result':{}})
  emit({'method':'item/completed','params':{'threadId':'child','item':{'type':'agentMessage','text':'Child done; parent handles publication'}}})
  emit({'method':'thread/tokenUsage/updated','params':{'threadId':'child','tokenUsage':{'total':{'inputTokens':999}}}})
  emit({'method':'turn/completed','params':{'threadId':'child','turn':{'status':'completed'}}})
  emit({'id':7,'method':'item/commandExecution/requestApproval','params':{'command':'echo fixture','itemId':'i'}})
 elif i==7:
  assert x['result']=={'decision':'accept'}
  emit({'method':'item/completed','params':{'threadId':'fixture','item':{'type':'agentMessage','text':'Verified fixture'}}})
  emit({'method':'thread/tokenUsage/updated','params':{'threadId':'fixture','tokenUsage':{'total':{'inputTokens':3,'outputTokens':2}}}})
  emit({'method':'turn/completed','params':{'threadId':'fixture','turn':{'status':'completed'}}})
  break
''',encoding='utf-8')
            rpc=Mock()
            rpc.call.side_effect=lambda name,args:{'id':'request-one'} if args['action']=='request' else {'state':'answered','answer':'Approve once'}
            output=io.StringIO()
            with contextlib.redirect_stdout(output):result=client.run([sys.executable,str(server)],1,9,folder,'fixture prompt',rpc=rpc,full_access=True)
            events=[json.loads(line) for line in output.getvalue().splitlines()]
            self.assertNotIn('Child done',output.getvalue())
            self.assertEqual(result,0)
            self.assertEqual(events[-1]['usage']['input_tokens'],3)
            self.assertEqual(events[-2]['item']['text'],'Verified fixture')
