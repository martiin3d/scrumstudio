import importlib.util
import json
from pathlib import Path
import sys
import time
import tempfile
import tomllib
import unittest
from unittest.mock import patch,Mock

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import provider_usage
import agent_bridge

class BridgeTest(unittest.TestCase):
    def test_idle_bridge_uses_few_checkpoints_and_notices_control_marker(self):
        import idle_signals
        for signal_at in (None,6):
            with self.subTest(signal_at=signal_at),tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as temporary:
                folder=Path(temporary);(folder/'.scrum/local').mkdir(parents=True)
                clock=[0];observed=[];offers=[];rpc=Mock()
                def call(name,args):
                    if name=='projects':return [{'id':1,'uuid':'fixture','checkout':'fixture','path':str(folder),'available':True}]
                    if name=='user_register':return {'id':9}
                    if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                    if name=='project_settings':return {'usage_check_seconds':300}
                    if name=='agent_checkpoint' and args['action']=='observe':
                        observed.append(clock[0]);return {'should_stop':False,'reason':'','control':{'generation':int(signal_at is not None and clock[0]>=signal_at),'paused':False}}
                    if name=='worker_launch' and args.get('action')=='reserve':offers.append(clock[0]);return {'eligible':False,'reason':'No eligible work'}
                    return {}
                def sleep(seconds):
                    clock[0]+=seconds
                    if clock[0]==signal_at:(folder/'.scrum/local/control.signal').write_text('wakeup')
                    if clock[0]>=120:raise KeyboardInterrupt
                rpc.call.side_effect=call
                pool=Mock();future=Mock();future.done.return_value=True;future.result.return_value={'session_used_percent':1,'weekly_used_percent':1};pool.submit.return_value=future
                with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=pool),patch.object(agent_bridge.time,'monotonic',side_effect=lambda:clock[0]),patch.object(agent_bridge.time,'sleep',side_effect=sleep),patch.object(agent_bridge.subprocess,'Popen') as spawn:
                    agent_bridge.main()
                spawn.assert_not_called();pool.submit.assert_called_once()
                self.assertLessEqual(len(observed),4)
                if signal_at:self.assertEqual(offers,[6])
                else:self.assertEqual(offers,[])

    def test_git_recovery_targets_one_offer_without_idle_model_retry(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as temporary:
            folder=Path(temporary);clock=[0];offers=[];rpc=Mock()
            def call(name,args):
                if name=='projects':return [{'id':1,'uuid':'fixture','checkout':'fixture','path':str(folder),'available':True}]
                if name=='user_register':return {'id':9}
                if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                if name=='project_settings':return {'usage_check_seconds':300}
                if name=='agent_checkpoint':return {'should_stop':False,'reason':'','git_recovery_id':42,'control':{'generation':0,'paused':False}}
                if name=='worker_launch' and args.get('action')=='reserve':
                    offers.append(args);return {'eligible':False,'reason':'Recovery unavailable'}
                return {}
            def sleep(seconds):
                clock[0]+=seconds
                if clock[0]>=180:raise KeyboardInterrupt
            rpc.call.side_effect=call
            pool=Mock();future=Mock();future.done.return_value=True;future.result.return_value={'session_used_percent':1,'weekly_used_percent':1};pool.submit.return_value=future
            with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=pool),patch.object(agent_bridge.time,'monotonic',side_effect=lambda:clock[0]),patch.object(agent_bridge.time,'sleep',side_effect=sleep),patch.object(agent_bridge.subprocess,'Popen') as spawn:
                agent_bridge.main()
            spawn.assert_not_called();self.assertEqual(len(offers),1);self.assertEqual(offers[0]['id'],42)

    def test_preclaim_failure_is_reported_instead_of_delegated_to_model(self):
        rpc=Mock();rpc.call.side_effect=RuntimeError('Reservation denied')
        result=agent_bridge.preclaim(rpc,1,9,'codex',70,{})
        self.assertTrue(result['should_stop']);self.assertIn('Reservation denied',result['reason'])
        rpc.call.side_effect=None;rpc.call.return_value={'claim_deferred':'File reservation conflict'}
        result=agent_bridge.preclaim(rpc,1,9,'codex',70,{})
        self.assertTrue(result['should_stop']);self.assertIn('File reservation conflict',result['reason'])

    def test_plain_config_startup_failure_is_reported_without_other_log_content(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as folder:
            path=Path(folder)/'worker.log'
            path.write_text('unrelated secret text\nError loading config.toml: invalid transport\nin `mcp_servers.blockbench`\n',encoding='utf-8')
            message=agent_bridge.run_failure(path,0)
            self.assertIn('invalid transport',message);self.assertIn('blockbench',message)
            self.assertNotIn('secret',message)

    def test_empty_or_failed_preflight_never_starts_model(self):
        for failure in (False,True):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as temporary:
                folder=Path(temporary);rpc=Mock()
                def call(name,args):
                    if name=='projects':return [{'id':1,'uuid':'fixture','checkout':'fixture','path':str(folder),'available':True}]
                    if name=='user_register':return {'id':9}
                    if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                    if name=='project_settings':return {'usage_check_seconds':60}
                    if name=='agent_checkpoint':return {'should_stop':False,'reason':'','control':{'generation':1,'paused':False}}
                    if name=='worker_launch' and args.get('action')=='reserve':
                        if failure:raise RuntimeError('Preflight offline')
                        return {'eligible':False,'reason':'No eligible work'}
                    return {}
                rpc.call.side_effect=call
                pool=Mock();pending=Mock();pending.done.return_value=True;pending.result.return_value={};pool.submit.return_value=pending
                with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=pool),patch.object(agent_bridge.subprocess,'Popen') as spawn,patch.object(agent_bridge.time,'sleep',side_effect=[None,KeyboardInterrupt]):
                    agent_bridge.main()
                spawn.assert_not_called()
                self.assertTrue(any('no model' in entry.args[1].get('text','').lower() or 'No eligible' in entry.args[1].get('text','') for entry in rpc.call.call_args_list if entry.args[0]=='project_chat'))

    def test_explicit_models_and_provider_defaults(self):
        with patch.object(agent_bridge,'codex_executable',return_value='codex'),patch.object(agent_bridge.shutil,'which',return_value='claude'):
            for client in ('codex','claude'):
                self.assertNotIn('--model',agent_bridge.command(client,Path.cwd(),'Task'))
                args=agent_bridge.command(client,Path.cwd(),'Task','fixture-model')
                self.assertEqual(args[args.index('--model')+1],'fixture-model')
                with self.assertRaises(ValueError):agent_bridge.command(client,Path.cwd(),'Task','--unsafe')

    def test_reported_model_and_exact_mismatch(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as folder:
            path=Path(folder)/'worker.log'
            path.write_text(json.dumps({'type':'system','subtype':'init','model':'claude-fixture'})+'\n',encoding='utf-8')
            self.assertEqual(agent_bridge.reported_model(path,0),'claude-fixture')
            self.assertFalse(agent_bridge.model_mismatch('opus','claude-fixture'))
            self.assertFalse(agent_bridge.model_mismatch('claude-fixture[1m]','claude-fixture'))
            self.assertTrue(agent_bridge.model_mismatch('claude-other','claude-fixture'))

    def test_verified_auto_resume_survives_pending_telemetry_and_launches_once(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as temporary:
            folder=Path(temporary);(folder/'.scrum/local').mkdir(parents=True)
            rpc=Mock();observations=0
            def call(name,args):
                nonlocal observations
                if name=='work_start':return {'claim':{'claimed':True,'token':'fixture'},'task':{'id':1,'code':'FIX-1'}}
                if name=='worker_launch':return {'eligible':True,'id':1,'code':'FIX-1'} if args.get('action')=='reserve' else {'released':True}
                if name=='projects':return [{'id':1,'uuid':'fixture','checkout':'fixture','path':str(folder),'available':True}]
                if name=='user_register':return {'id':9}
                if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                if name=='project_settings':return {'usage_check_seconds':60,'auto_resume_session_quota':True}
                if name=='agent_checkpoint' and args['action']=='observe':
                    observations+=1
                    return {'should_stop':False,'reason':'','control':{'generation':0,'paused':False},'auto_resume':{'requested':observations==1,'status':'session reset verified; automatic resume requested'}}
                return {}
            rpc.call.side_effect=call
            child=Mock();child.poll.return_value=None
            pool=Mock();future=Mock();future.done.return_value=True;future.result.return_value={}
            pool.submit.return_value=future
            with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=pool),patch.object(agent_bridge,'command',return_value=['fixture-cli']),patch.object(agent_bridge.subprocess,'Popen',return_value=child) as spawn,patch.object(agent_bridge,'stop_child'),patch.object(agent_bridge.time,'sleep',side_effect=[None,None,KeyboardInterrupt]):
                agent_bridge.main()
            spawn.assert_called_once()
            chats=[c.args[1]['text'] for c in rpc.call.call_args_list if c.args[0]=='project_chat']
            self.assertTrue(any(t.startswith('Session quota reset ->') for t in chats))

    def test_provider_windows_are_used_percent_not_remaining(self):
        data={'rateLimitsByLimitId':{'main':{'primary':{'windowDurationMins':300,'usedPercent':92,'resetsAt':time.time()+1000},'secondary':{'windowDurationMins':10080,'usedPercent':94}},'other':{'primary':{'windowDurationMins':15,'usedPercent':99},'secondary':None}}}
        result=provider_usage.normalize_codex(data)
        self.assertEqual((result['session_used_percent'],result['weekly_used_percent']),(92,94))
        self.assertNotIn('session_used_percent',provider_usage.normalize_codex({'rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':80,'resetsAt':1}}}))
        self.assertEqual(provider_usage.claude_payload({}),{})
        self.assertEqual(provider_usage.claude_payload({'rate_limits':{'five_hour':{'used_percentage':92},'seven_day':{'used_percentage':94}}})['weekly_used_percent'],94)

    def test_no_model_start_without_explicit_bridge_opt_in(self):
        with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex']),patch.object(agent_bridge,'ScrumClient') as rpc:
            with self.assertRaises(SystemExit):agent_bridge.main()
            rpc.assert_not_called()

    def test_cli_commands_preserve_permission_checks(self):
        with patch.object(agent_bridge.shutil,'which',return_value='cli'):
            codex=agent_bridge.command('codex',Path.cwd(),'Task')
            claude=agent_bridge.command('claude',Path.cwd(),'Task')
        self.assertEqual(codex[-1],'-')
        self.assertNotIn('Task',codex);self.assertNotIn('Task',claude)
        self.assertIn('permissions.scrum-worker.extends=":workspace"',codex)
        read_arg=next(arg for arg in codex if arg.startswith('permissions.scrum-worker.filesystem='))
        reads=tomllib.loads('filesystem='+read_arg.split('=',1)[1])['filesystem']
        self.assertEqual(len(reads),3)
        self.assertTrue(all(value=='read' for value in reads.values()))
        self.assertTrue(any(path.endswith('/.codex/skills') for path in reads))
        self.assertIn('approval_policy="on-request"',codex)
        self.assertIn('approvals_reviewer="auto_review"',codex)
        self.assertIn('mcp_servers.scrum-studio.default_tools_approval_mode="approve"',codex)
        allowlist=json.loads(next(arg.split('=',1)[1] for arg in codex if arg.startswith('mcp_servers.scrum-studio.enabled_tools=')))
        self.assertIn('task_claim',allowlist)
        self.assertTrue({'user_manage','agent_manage','project_control','project_settings'}.isdisjoint(allowlist))
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',codex)
        self.assertNotIn('--dangerously-skip-permissions',claude)

    def test_claude_worker_uses_compact_preapproved_scrum_catalog(self):
        with patch.object(agent_bridge.shutil,'which',return_value='claude'):
            claude=agent_bridge.command('claude',Path.cwd(),'Task')
        self.assertNotIn('--strict-mcp-config',claude)
        config=Path(next(arg.split('=',1)[1] for arg in claude if arg.startswith('--mcp-config=')))
        server=json.loads(config.read_text(encoding='utf-8'))['mcpServers']['scrum-studio']
        self.assertIn('--worker-schema',server['args'])
        allowed=next(arg.split('=',1)[1] for arg in claude if arg.startswith('--allowedTools=')).split(',')
        self.assertIn('mcp__scrum-studio__work_start',allowed)
        self.assertFalse(any('project_control' in name or name=='Bash' for name in allowed))
        self.assertIn('--permission-mode=acceptEdits',claude)

    def test_exited_child_is_never_signalled(self):
        child=Mock();child.poll.return_value=0
        agent_bridge.stop_child(child)
        child.send_signal.assert_not_called()

    def test_wakeup_starts_one_child_and_stop_saves_handoff_after_exit(self):
        root=SCRIPTS.parent/'data'
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder=Path(temporary);(folder/'.scrum/local').mkdir(parents=True)
            rpc=Mock();events=[];observations=0
            def call(name,args):
                nonlocal observations
                if name=='work_start':return {'claim':{'claimed':True,'token':'fixture'},'task':{'id':1,'code':'FIX-1'}}
                if name=='worker_launch':return {'eligible':True,'id':1,'code':'FIX-1'} if args.get('action')=='reserve' else {'released':True}
                if name=='projects':return [{'id':1,'uuid':'project','checkout':'checkout','path':str(folder),'available':True}]
                if name=='user_register':return {'id':9}
                if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                if name=='project_settings':return {'usage_check_seconds':60}
                if name=='agent_checkpoint' and args['action']=='observe':
                    observations+=1
                    return {'should_stop':observations>2,'reason':'Human Stop','control':{'generation':observations,'paused':observations>2}}
                events.append(args.get('action'));return {}
            rpc.call.side_effect=call
            child=Mock();child.poll.return_value=None
            usage_pool=Mock();pending=Mock();pending.done.return_value=True;pending.result.return_value={}
            usage_pool.submit.return_value=pending
            def stopped(proc):events.append('process-exited');proc.poll.return_value=0
            with patch.object(agent_bridge,'final_message',return_value='Human explanation only'),patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=usage_pool),patch.object(agent_bridge,'command',return_value=['fake-cli']),patch.object(agent_bridge.subprocess,'Popen',return_value=child) as spawn,patch.object(agent_bridge,'stop_child',side_effect=stopped),patch.object(agent_bridge.time,'sleep',side_effect=[None,None,KeyboardInterrupt]):
                agent_bridge.main()
            spawn.assert_called_once()
            chat=[call.args[1]['text'] for call in rpc.call.call_args_list if call.args[0]=='project_chat']
            self.assertTrue(any(text.startswith('Wakeup -> codex') for text in chat))
            self.assertTrue(any('interrupted' in text for text in chat))
            self.assertLess(events.index('process-exited'),events.index('pause'))
            pause=next(call.args[1] for call in rpc.call.call_args_list if call.args[0]=='agent_checkpoint' and call.args[1]['action']=='pause')
            self.assertNotIn('Human explanation only',pause['summary'])
            self.assertTrue(any('Human explanation only' in text for text in chat))
            pending.result.assert_called_once() # Launch waits for the initial telemetry result.


class BridgeChatTest(unittest.TestCase):
    def test_provider_failure_excludes_raw_catalog_and_tool_output(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as folder:
            path=Path(folder)/'run.log'
            message=json.dumps({'error':{'message':'Configured model is not supported'}})
            events=['raw catalog containing private configuration',json.dumps({'type':'item.completed','item':{'type':'command_execution','text':'tool-secret'}}),json.dumps({'type':'error','message':message})]
            path.write_text('\n'.join(events),encoding='utf-8')
            self.assertEqual(agent_bridge.run_failure(path,0),'Configured model is not supported')
    def test_bounded_final_output_for_both_clients_excludes_tools(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as folder:
            path=Path(folder)/'worker.log'
            path.write_text(json.dumps({'type':'result','result':'old response'})+'\n',encoding='utf-8')
            offset=path.stat().st_size
            with path.open('a',encoding='utf-8') as log:
                for item in [{'type':'item.completed','item':{'type':'agent_message','text':'Codex final'}},{'type':'item.completed','item':{'type':'command_execution','text':'private tool output'}}]:log.write(json.dumps(item)+'\n')
            self.assertEqual(agent_bridge.final_message(path,offset),'Codex final')
            path.write_text(json.dumps({'type':'result','result':'Claude final'})+'\n',encoding='utf-8')
            self.assertEqual(agent_bridge.final_message(path,0),'Claude final')
            path.write_text(json.dumps({'type':'result','result':'x'*4000})+'\n',encoding='utf-8')
            self.assertEqual(len(agent_bridge.final_message(path,0)),1500)
            path.write_text('invalid json\n{}\n',encoding='utf-8')
            self.assertEqual(agent_bridge.final_message(path,0),'')
    def test_chat_failure_does_not_abort_worker(self):
        rpc=Mock();rpc.call.side_effect=RuntimeError('offline')
        agent_bridge.chat_event(rpc,1,2,'notice')
    def test_chat_uses_bridge_author_and_bounded_text(self):
        rpc=Mock();agent_bridge.chat_event(rpc,1,9,'x'*3000)
        args=rpc.call.call_args.args[1]
        self.assertEqual(args['actor'],9)
        self.assertEqual(len(args['text']),2000)


class ManagedBridgeTest(unittest.TestCase):
    def test_each_completed_task_gets_its_own_model_before_next_launch(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as temp:
            folder=Path(temp);(folder/'.scrum/local').mkdir(parents=True)
            rpc=Mock();offers=[]
            def call(name,args):
                if name=='projects':return [{'id':1,'uuid':'fixture','checkout':'fixture','path':str(folder),'available':True}]
                if name=='agent_manage':return {'actor':9,'client':'codex','instructions':'','generation':1,'model':'profile-model'}
                if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                if name=='project_settings':return {'usage_check_seconds':60}
                if name=='agent_checkpoint' and args['action']=='observe':return {'should_stop':False,'reason':'','profile_enabled':True,'control':{'generation':0,'paused':False}}
                if name=='work_start':return {'claim':{'claimed':True,'token':'fixture'},'task':{'id':args['id'],'code':'FIX-'+str(args['id'])}}
                if name=='task_get':return {'status':'Done'}
                if name=='worker_launch' and args.get('action')=='reserve':
                    if len(offers)==2:return {'eligible':False,'reason':'No work'}
                    model='task-'+str(len(offers)+1);offers.append(model)
                    return {'eligible':True,'id':len(offers),'code':'FIX-'+str(len(offers)),'selection':{'model':model,'source':'task'}}
                return {}
            rpc.call.side_effect=call
            pool=Mock();future=Mock();future.done.return_value=True;future.result.return_value={};pool.submit.return_value=future
            child=Mock();child.poll.return_value=0;child.returncode=0
            with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run','--profile','a'*32,'--start-now']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=pool),patch.object(agent_bridge,'command',return_value=['fake']) as command,patch.object(agent_bridge.subprocess,'Popen',return_value=child) as spawn,patch.object(agent_bridge.time,'sleep',side_effect=[None,None,KeyboardInterrupt]):
                agent_bridge.main()
            self.assertEqual(spawn.call_count,2)
            self.assertEqual([entry.args[3] for entry in command.call_args_list],['task-1','task-2'])
            self.assertTrue(all('Work ONLY on FIX-' in entry.args[2] for entry in command.call_args_list))

    def test_immediate_profile_start_uses_role_and_stable_actor(self):
        with tempfile.TemporaryDirectory(dir=SCRIPTS.parent/'data') as temp:
            folder=Path(temp);(folder/'.scrum/local').mkdir(parents=True)
            workspace=folder/'isolated';workspace.mkdir()
            rpc=Mock()
            def call(name,args):
                if name=='work_start':return {'claim':{'claimed':True,'token':'fixture','git':{'worktree':str(workspace)}},'task':{'id':1,'code':'FIX-1'}}
                if name=='worker_launch':return {'eligible':True,'id':1,'code':'FIX-1'} if args.get('action')=='reserve' else {'released':True}
                if name=='projects':return [{'id':1,'uuid':'project','checkout':'checkout','path':str(folder),'available':True}]
                if name=='agent_manage':return {'actor':9,'client':'codex','instructions':'You review performance.','generation':1,'model':'fixture-model'}
                if name=='agent_status':return {'agents':[],'control':{'generation':0}}
                if name=='project_settings':return {'usage_check_seconds':60}
                if name=='agent_checkpoint' and args['action']=='observe':return {'should_stop':False,'profile_enabled':True,'profile_generation':1,'control':{'generation':0,'paused':False}}
                return {}
            rpc.call.side_effect=call
            pool=Mock();future=Mock();future.done.return_value=True;future.result.return_value={'session_used_percent':1,'weekly_used_percent':1};pool.submit.return_value=future
            child=Mock();child.poll.return_value=None
            captured=[]
            def spawned(*args,**kwargs):
                captured.append(kwargs['stdin'].read().decode('utf-8'));return child
            with patch.object(sys,'argv',['agent_bridge.py','--project','1','--client','codex','--allow-run','--profile','a'*32,'--start-now']),patch.object(agent_bridge,'ScrumClient',return_value=rpc),patch.object(agent_bridge,'ThreadPoolExecutor',return_value=pool),patch.object(agent_bridge,'command',return_value=['fake']) as command,patch.object(agent_bridge.subprocess,'Popen',side_effect=spawned) as spawn,patch.object(agent_bridge,'stop_child'),patch.object(agent_bridge.time,'sleep',side_effect=[None,KeyboardInterrupt]):
                agent_bridge.main()
            spawn.assert_called_once()
            self.assertEqual(spawn.call_args.kwargs['cwd'],workspace)
            self.assertEqual(command.call_args.args[1],workspace)
            self.assertEqual(command.call_args.kwargs['policy_folder'],folder)
            prompt=command.call_args.args[2]
            self.assertEqual(command.call_args.args[3],'fixture-model')
            self.assertIn('relative to each skill directory, never the target checkout',prompt)
            self.assertIn('navigation only: load current full scope with task_get or work_start(id)',prompt)
            self.assertIn('acceptance criteria, blockers, checklist, evidence and continuity',prompt)
            self.assertEqual(captured,[prompt])
            self.assertIn('You review performance.',prompt)
            self.assertIn('persistent agent actor is 9',prompt)
            self.assertIn('supervisor already ran work_start and claimed',prompt)
            self.assertIn('Do NOT call work_start or task_claim again',prompt)
            self.assertIn('In Progress',prompt)
            self.assertIn('continue with the next eligible task',prompt)
            self.assertIn(json.dumps(sys.executable),prompt)
            self.assertIn('--usage-once',prompt)
            self.assertIn('--timeout 15',prompt)
            self.assertIn('Quota is supervised outside the model',prompt)
            self.assertIn('Full task',prompt)
            self.assertIn('acceptance criteria',prompt)
            self.assertIn('do not weaken them',prompt)
            self.assertFalse(any(c.args[0]=='user_register' for c in rpc.call.call_args_list))
            self.assertTrue(any(c.args[0]=='agent_checkpoint' and c.args[1].get('profile_id')=='a'*32 for c in rpc.call.call_args_list))
