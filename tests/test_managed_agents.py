import unittest
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_storage
from store import run

class ManagedAgentTest(unittest.TestCase):
    def test_supervisor_can_reconnect_without_requesting_model_work(self):
        p,_=self.project();profile=self.create(p,start_immediately=False)
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            result=run('agent_manage',dict(project=p,actor=self.human,id=profile['id'],action='start',start_immediately=False))
        self.assertTrue(result['enabled'])
        self.assertNotIn('--start-now',spawn.call_args.args[0])
        self.assertIn('--allow-run',spawn.call_args.args[0])
        run('agent_checkpoint',dict(project=p,actor=profile['actor'],profile_id=profile['id'],client='codex',action='observe',running=False,session_used_percent=1,weekly_used_percent=1))
        before=run('agent_manage',dict(project=p,id=profile['id'],action='get'))['generation']
        with patch('managed_agents.subprocess.Popen') as spawn:
            connected=run('agent_manage',dict(project=p,actor=self.human,id=profile['id'],action='start',start_immediately=False))
        self.assertEqual(connected['request'],'already_connected')
        self.assertEqual(connected['generation'],before)
        spawn.assert_not_called()

    def test_model_persistence_and_next_run_update(self):
        p,_=self.project()
        for client in ('codex','claude'):
            with self.subTest(client=client),patch('managed_agents.subprocess.Popen') as spawn:
                profile=run('agent_manage',dict(project=p,actor=self.human,action='create',name=client,client=client,model='fixture-model',start_immediately=False))
                self.assertEqual(profile['model'],'fixture-model')
                changed=run('agent_manage',dict(project=p,actor=self.human,action='update',id=profile['id'],model='next-model'))
                self.assertEqual(changed['generation'],profile['generation'])
                self.assertEqual(run('agent_manage',dict(project=p,action='get',id=profile['id']))['model'],'next-model')
                report=run('agent_checkpoint',dict(project=p,actor=profile['actor'],profile_id=profile['id'],client=client,session_used_percent=1,weekly_used_percent=1))
                self.assertEqual(report['profile_model'],'next-model')
                run('agent_manage',dict(project=p,actor=self.human,action='update',id=profile['id'],model=''))
                self.assertEqual(run('agent_manage',dict(project=p,action='get',id=profile['id']))['model'],'')
                spawn.assert_not_called()

    def test_model_validation_and_update_authorization(self):
        p,_=self.project()
        profile=self.create(p,start_immediately=False)
        self.assertEqual(profile['model'],'')
        for value in (None,42,'--unsafe','has space','x\noption','x'*257):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'Model'):
                run('agent_manage',dict(project=p,actor=self.human,action='update',id=profile['id'],model=value))
        with self.assertRaisesRegex(ValueError,'human'):
            run('agent_manage',dict(project=p,actor=self.agent,action='update',id=profile['id'],model='fixture'))

    def setUp(self):
        test_storage.StorageTest.setUp(self)
        resolver=patch('managed_agents.codex_executable',side_effect=lambda:__import__('managed_agents').shutil.which('codex'))
        resolver.start();self.addCleanup(resolver.stop)
    tearDown=test_storage.StorageTest.tearDown
    def project(self,name='Project'):
        project,folder=test_storage.StorageTest.project(self,name)
        run('project_settings',dict(project=project,actor=self.human,max_parallel_agents=1))
        return project,folder
    def create(self,p,**extra):
        return run('agent_manage',dict(project=p,action='create',actor=self.human,name='Builder',**extra))
    def wake(self,p,**extra):
        return run('project_control',dict(project=p,actor=self.human,action='wakeup',**extra))

    def test_provider_settings_persist_and_gate_the_wakeup_roster(self):
        for codex,claude in ((True,True),(True,False),(False,True),(False,False)):
            with self.subTest(codex=codex,claude=claude):
                p,folder=self.project()
                policy={'codex_enabled':codex,'claude_enabled':claude}
                with patch('managed_agents.shutil.which',side_effect=lambda client:client),patch('managed_agents.subprocess.Popen') as spawn:
                    for client in ('codex','claude'):
                        run('agent_manage',dict(project=p,actor=self.human,action='create',name=client,client=client,
                            usage_file=str(folder/'usage.json'),confirm_client_account=True))
                    spawn.reset_mock()
                    run('project_settings',dict(project=p,actor=self.human,**policy))
                    reopened=run('project_open',dict(path=str(folder)))['id']
                    settings=run('project_settings',dict(project=reopened))
                    self.assertEqual({key:settings[key] for key in policy},policy)
                    for client,enabled in (('codex',codex),('claude',claude)):
                        if enabled:continue
                        profile=next(row for row in run('agent_manage',dict(project=p))['agents'] if row['client']==client)
                        with self.assertRaisesRegex(ValueError,'disabled in Project Settings'):
                            run('agent_manage',dict(project=p,actor=self.human,action='start',id=profile['id'],confirm_client_account=True))
                    with patch('managed_agents.time.time',return_value=time.time()+21):
                        self.wake(p,confirm_client_account=True)
                    self.assertEqual([call.args[0][call.args[0].index('--client')+1] for call in spawn.call_args_list],
                                     [client for client,enabled in (('claude',claude),('codex',codex)) if enabled])

    def test_provider_defaults_for_legacy_snapshot_and_no_disabled_default_launch(self):
        p,folder=self.project()
        run('project_settings',dict(project=p,actor=self.human,codex_enabled=False,claude_enabled=False))
        with patch('managed_agents.subprocess.Popen') as spawn:
            self.assertEqual(self.wake(p)['wakeup']['status'],'providers_disabled')
            spawn.assert_not_called()
        self.assertEqual(run('agent_manage',dict(project=p))['agents'],[])
        run('project_export',dict(project=p))
        clone=test_storage.StorageTest.clone
        restored,_=clone(self,folder)
        persisted=run('project_settings',dict(project=restored['id']))
        self.assertFalse(persisted['codex_enabled'])
        self.assertFalse(persisted['claude_enabled'])
        snapshot=folder/'.scrum/snapshot.json'
        data=json.loads(snapshot.read_text(encoding='utf-8'))
        for key in ('codex_enabled','claude_enabled'):
            data['settings'].pop(key)
        snapshot.write_text(json.dumps(data),encoding='utf-8')
        legacy,_=clone(self,folder)
        settings=run('project_settings',dict(project=legacy['id']))
        self.assertTrue(settings['codex_enabled'])
        self.assertTrue(settings['claude_enabled'])

    def test_disabled_offer_cannot_be_claimed_or_block_enabled_worker(self):
        p,_=self.project()
        codex=self.create(p,start_immediately=False)['actor']
        claude=run('agent_manage',dict(project=p,actor=self.human,action='create',name='Claude',client='claude',start_immediately=False))['actor']
        for client,actor in (('codex',codex),('claude',claude)):
            run('agent_checkpoint',dict(project=p,actor=actor,client=client,session_used_percent=10,weekly_used_percent=10))
        fixed=run('task_create',dict(project=p,title='Codex task',description='Bounded outcome',acceptance_criteria=['Verified'],model='gpt-6.2-sol',actor=self.human))['id']
        shared=run('task_create',dict(project=p,title='Shared task',description='Bounded outcome',acceptance_criteria=['Verified'],files=['shared.py']))['id']
        successor=run('task_create',dict(project=p,title='Next shared task',description='Bounded outcome',acceptance_criteria=['Verified'],files=['shared.py']))['id']
        run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=2))
        self.assertEqual(run('worker_launch',dict(project=p,actor=codex,client='codex',id=fixed))['id'],fixed)
        self.assertEqual(run('worker_launch',dict(project=p,actor=claude,client='claude'))['id'],shared)
        for actor in (codex,claude):
            run('worker_launch',dict(project=p,actor=actor,action='release'))
        run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=1))
        offer=run('worker_launch',dict(project=p,actor=codex,client='codex',id=shared))
        self.assertEqual(offer['id'],shared)
        self.assertFalse(run('worker_launch',dict(project=p,actor=claude,client='claude'))['eligible'])
        run('project_settings',dict(project=p,actor=self.human,codex_enabled=False))
        for operation,extra in [('task_claim',{}),('task_update',{'claim':True})]:
            with self.assertRaisesRegex(ValueError,'Codex is disabled'):
                run(operation,dict(project=p,id=shared,actor=codex,**extra))
        self.assertFalse(run('worker_launch',dict(project=p,actor=codex,client='codex'))['eligible'])
        offer=run('worker_launch',dict(project=p,actor=claude,client='claude'))
        self.assertEqual(offer['id'],shared)
        self.assertEqual([task['id'] for task in offer['batch']],[shared,successor])
        claim=run('task_claim',dict(project=p,id=shared,actor=claude))
        self.assertTrue(claim['claimed'])
        run('project_settings',dict(project=p,actor=self.human,claude_enabled=False))
        finished=run('work_finish',dict(project=p,actor=claude,id=shared,token=claim['token'],client='claude',
            evidence='Tested',use_batch=True,claim_next=True))
        self.assertEqual(finished['completed']['status'],'Done')
        self.assertTrue(finished['next']['batch_done'])
        self.assertNotIn('claim',finished['next'])
        self.assertEqual(run('task_get',dict(project=p,id=fixed))['status'],'TODO')
        self.assertEqual(run('task_get',dict(project=p,id=successor))['status'],'TODO')

    def test_deleted_managed_identity_is_marked_and_can_be_restored(self):
        p,_=self.project()
        profile=self.create(p,start_immediately=False)
        run('agent_checkpoint',dict(project=p,actor=profile['actor'],client='codex',action='offline',session_used_percent=10,weekly_used_percent=20))
        task=run('task_create',dict(project=p,title='Recover pending work',description='Recover abandoned work through existing deletion.',acceptance_criteria=['Task becomes available'],files=['pending.py']))['id']
        run('task_claim',dict(project=p,id=task,actor=profile['actor'],files=['pending.py']))
        result=run('user_manage',dict(actor=self.human,id=profile['actor'],action='delete'))
        self.assertFalse(result.get('pending',False))
        self.assertTrue(run('agent_manage',dict(project=p))['agents'][0]['deleted'])
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'TODO')
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])
        with self.assertRaisesRegex(ValueError,'deleted'):
            run('agent_manage',dict(project=p,actor=self.human,id=profile['id'],action='start'))
        run('user_manage',dict(actor=self.human,id=profile['actor'],action='restore'))
        restored=run('agent_manage',dict(project=p))['agents'][0]
        self.assertFalse(restored['deleted']);self.assertEqual(restored['id'],profile['id'])

    def test_empty_wakeup_creates_one_reusable_default_and_requests_immediate_start(self):
        p,_=self.project()
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            first=self.wake(p)
            second=self.wake(p)
        self.assertTrue(first['default_profile_created'])
        self.assertEqual(first['wakeup']['status'],'starting')
        self.assertEqual(first['launches'][0]['id'],second['launches'][0]['id'])
        spawn.assert_called_once()
        self.assertIn('--start-now',spawn.call_args.args[0])
        profiles=run('agent_manage',dict(project=p))['agents']
        self.assertEqual(len(profiles),1)
        self.assertEqual(profiles[0]['client'],'codex')
        self.assertTrue(profiles[0]['resume_on_wakeup'])

    def test_wakeup_restores_enabled_set_and_preserves_individual_stop(self):
        p,_=self.project()
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen'):
            first=self.create(p,instructions='Review rendering',source='reviewer.md')
            second=run('agent_manage',dict(project=p,actor=self.human,action='create',name='Other',instructions='Other role'))
        run('agent_manage',dict(project=p,actor=self.human,action='stop',id=second['id']))
        with patch('managed_agents.time.time',return_value=time.time()+21),patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p)
        spawn.assert_called_once()
        self.assertEqual([item['id'] for item in result['launches']],[first['id']])
        saved=run('agent_manage',dict(project=p,action='get',id=first['id'],include_instructions=True))
        self.assertEqual(saved['actor'],first['actor'])
        self.assertEqual(saved['instructions'],'Review rendering')
        stopped=run('agent_manage',dict(project=p,action='get',id=second['id']))
        self.assertFalse(stopped['resume_on_wakeup'])
        run('agent_manage',dict(project=p,actor=self.human,action='stop',id=first['id']))
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p)
            spawn.assert_not_called()
        self.assertEqual(result['wakeup']['status'],'profiles_stopped')
        self.assertEqual(len(run('agent_manage',dict(project=p))['agents']),2)

    def test_failed_default_can_retry_without_creating_another_profile(self):
        p,_=self.project()
        with patch('managed_agents.shutil.which',return_value=None),patch('managed_agents.subprocess.Popen') as spawn:
            failed=self.wake(p)
            spawn.assert_not_called()
        self.assertEqual(failed['wakeup']['status'],'launch_failed')
        self.assertIn('PATH',failed['wakeup']['next_step'])
        self.assertIn('PATH',run('agent_manage',dict(project=p))['agents'][0]['error'])
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            retried=self.wake(p)
            spawn.assert_called_once()
        self.assertEqual(retried['launches'][0]['id'],failed['launches'][0]['id'])

    def test_wakeup_does_not_duplicate_manual_or_managed_connected_bridges(self):
        p,_=self.project()
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='observe',running=False,session_used_percent=93,weekly_used_percent=2))
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p)
            spawn.assert_not_called()
        self.assertEqual(result['wakeup']['status'],'held')
        self.assertEqual(run('agent_manage',dict(project=p))['agents'],[])
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen'):
            profile=self.create(p)
        run('agent_checkpoint',dict(project=p,actor=profile['actor'],profile_id=profile['id'],client='codex',action='observe',running=True,session_used_percent=1,weekly_used_percent=2))
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p)
            spawn.assert_not_called()
        self.assertEqual(result['launches'][0]['request'],'signal_sent')

    def test_wakeup_claude_requires_account_authorization_without_provider_fallback(self):
        p,_=self.project()
        run('project_settings',dict(project=p,actor=self.human,codex_enabled=False))
        with patch('managed_agents.shutil.which',return_value='claude'),patch('managed_agents.subprocess.Popen'):
            profile=self.create(p,client='claude',usage_file=str(self.root/'usage.json'),confirm_client_account=True)
        with patch('managed_agents.time.time',return_value=time.time()+21),patch('managed_agents.subprocess.Popen') as spawn:
            held=self.wake(p)
            spawn.assert_not_called()
        self.assertEqual(held['launches'][0]['request'],'authorization_required')
        self.assertEqual(held['launches'][0]['client'],'claude')
        with patch('managed_agents.time.time',return_value=time.time()+21),patch('managed_agents.shutil.which',return_value='claude'),patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p,confirm_client_account=True)
            spawn.assert_called_once()
        self.assertEqual(result['launches'][0]['id'],profile['id'])

    def test_parallel_wakeup_does_not_duplicate_default_profile_or_bridge(self):
        p,_=self.project()
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn,ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.wake(p),range(2)))
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(len(run('agent_manage',dict(project=p))['agents']),1)
        self.assertTrue(all(result['wakeup']['status']=='starting' for result in results),results)

    def test_play_fills_capacity_once_and_both_agents_can_claim_distinct_work(self):
        p,_=self.project()
        run('project_settings',dict(project=p,actor=self.human,max_parallel_agents=2,file_lock_policy='Auto'))
        stopped=run('agent_manage',dict(project=p,actor=self.human,action='create',name='Stopped',start_immediately=False))
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            saved=self.create(p)
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _:self.wake(p),range(2)))
            self.wake(p)
        agents=run('agent_manage',dict(project=p))['agents']
        enabled=[agent for agent in agents if agent['enabled']]
        self.assertEqual(len(agents),3)
        self.assertEqual(len({agent['actor'] for agent in enabled}),2)
        self.assertIn(saved['id'],{agent['id'] for agent in enabled})
        self.assertNotIn(stopped['id'],{agent['id'] for agent in enabled})
        self.assertEqual(spawn.call_count,2)
        for filename in ('a.py','b.py'):
            run('task_create',dict(project=p,title=filename,description='Independent work',acceptance_criteria=['Verified'],files=[filename]))
        claims=[]
        for agent in enabled:
            actor=agent['actor']
            run('agent_checkpoint',dict(project=p,actor=actor,profile_id=agent['id'],client='codex',
                action='observe',running=False,session_used_percent=1,weekly_used_percent=1))
            offer=run('worker_launch',dict(project=p,actor=actor,client='codex'))
            claim=run('task_claim',dict(project=p,actor=actor,id=offer['id']))
            self.assertTrue(claim['claimed'])
            self.assertEqual(claim['file_lock_mode'],'files')
            claims.append(offer['id'])
        self.assertEqual(len(set(claims)),2)

    def test_signal_only_wakeup_never_creates_or_launches_profiles(self):
        p,_=self.project()
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p,start_agents=False)
            spawn.assert_not_called()
        self.assertNotIn('launches',result)
        self.assertEqual(run('agent_manage',dict(project=p))['agents'],[])
        for invalid in [dict(start_agents='true'),dict(confirm_client_account=1)]:
            with self.assertRaises(ValueError):self.wake(p,**invalid)

    def test_wakeup_restores_multiple_saved_roles_without_inventing_profiles(self):
        p,_=self.project()
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen'):
            profiles=[run('agent_manage',dict(project=p,actor=self.human,action='create',name=name,instructions=name+' role')) for name in ['Builder','Reviewer']]
        with patch('managed_agents.time.time',return_value=time.time()+21),patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            result=self.wake(p)
        self.assertEqual(spawn.call_count,2)
        self.assertEqual({item['id'] for item in result['launches']},{item['id'] for item in profiles})
        self.assertEqual(len(run('agent_manage',dict(project=p))['agents']),2)

    def test_stop_between_discovery_and_restore_is_not_overridden(self):
        import managed_agents
        p,_=self.project()
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen'):
            profile=self.create(p)
        run('agent_manage',dict(project=p,actor=self.human,action='stop',id=profile['id']))
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=managed_agents.operation(dict(project=p,actor=self.human,action='start',id=profile['id'],_restore_only=True),run)
            spawn.assert_not_called()
        self.assertEqual(result['request'],'skipped_stopped')
    def test_create_immediate_stable_identity_and_compact_lists(self):
        p,folder=self.project()
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen') as spawn:
            result=self.create(p,instructions='Prefer focused changes',source='builder.md')
        self.assertEqual(result['request'],'starting');spawn.assert_called_once()
        command=spawn.call_args.args[0]
        self.assertIn('--start-now',command);self.assertIn(result['id'],command)
        self.assertNotIn('instructions',result)
        full=run('agent_manage',dict(project=p,action='get',id=result['id'],include_instructions=True))
        self.assertEqual(full['instructions'],'Prefer focused changes')
        self.assertEqual(run('agent_manage',dict(project=p))['agents'][0]['actor'],result['actor'])
        q,_=self.project('Other')
        self.assertEqual(run('agent_manage',dict(project=q))['agents'],[])
        with self.assertRaises(ValueError):run('agent_manage',dict(project=q,action='get',id=result['id']))
    def test_empty_role_stop_signal_and_auth(self):
        p,_=self.project();agent=self.create(p,start_immediately=False)
        self.assertEqual(agent['instruction_chars'],0)
        with self.assertRaises(ValueError):run('agent_manage',dict(project=p,action='stop',id=agent['id'],actor=self.agent))
        report=run('agent_checkpoint',dict(project=p,actor=agent['actor'],client='codex',profile_id=agent['id'],action='check',session_used_percent=1,weekly_used_percent=1))
        self.assertFalse(report['profile_enabled']);self.assertTrue(report['should_stop'])
        with self.assertRaises(ValueError):run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',profile_id=agent['id']))
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen'):
            run('agent_manage',dict(project=p,action='start',actor=self.human,id=agent['id']))
        report=run('agent_checkpoint',dict(project=p,actor=agent['actor'],client='codex',profile_id=agent['id'],action='observe',running=False,session_used_percent=1,weekly_used_percent=1))
        self.assertTrue(report['profile_enabled'])
        with patch('managed_agents.subprocess.Popen') as spawn:
            again=run('agent_manage',dict(project=p,action='start',actor=self.human,id=agent['id']))
            self.assertEqual(again['request'],'start_requested');spawn.assert_not_called()
        run('agent_manage',dict(project=p,action='stop',actor=self.human,id=agent['id']))
        self.assertFalse(run('agent_manage',dict(project=p,action='get',id=agent['id']))['enabled'])
    def test_validation_and_launch_failures(self):
        p,_=self.project()
        for extra in [dict(instructions='x'*8001),dict(source='role.exe'),dict(client='unknown'),dict(client='claude')]:
            with self.assertRaises(ValueError):self.create(p,**extra)
        self.assertEqual(run('agent_manage',dict(project=p))['agents'],[])
        with patch('managed_agents.shutil.which',return_value=None):result=self.create(p)
        self.assertEqual(result['request'],'launch_failed');self.assertIn('PATH',result['error'])
        with self.assertRaises(ValueError):self.create(p)
        with patch('managed_agents.shutil.which',return_value='codex'),patch('managed_agents.subprocess.Popen',side_effect=OSError('Cannot launch')):
            failure=run('agent_manage',dict(project=p,action='start',actor=self.human,id=result['id']))
        self.assertEqual(failure['request'],'launch_failed');self.assertFalse(failure['enabled'])
    def test_claude_preparation_never_runs_without_confirmation(self):
        p,_=self.project()
        agent=self.create(p,client='claude',start_immediately=False,usage_file=str(self.root/'usage.json'))
        with patch('managed_agents.subprocess.Popen') as spawn:
            with self.assertRaises(ValueError):run('agent_manage',dict(project=p,action='start',actor=self.human,id=agent['id']))
            spawn.assert_not_called()
        with patch('managed_agents.shutil.which',return_value='claude'),patch('managed_agents.subprocess.Popen') as spawn:
            run('agent_manage',dict(project=p,action='start',actor=self.human,id=agent['id'],confirm_client_account=True))
            self.assertIn('--usage-file',spawn.call_args.args[0])

    def test_toml_role_preserved_without_executing_configuration(self):
        p,_=self.project()
        instructions='name = "Reviewer"\ndeveloper_instructions = "Check accessibility."\nmodel = "example"\n'
        with patch('managed_agents.subprocess.Popen') as spawn:
            agent=self.create(p,instructions=instructions,source='reviewer.TOML',start_immediately=False)
            spawn.assert_not_called()
        saved=run('agent_manage',dict(project=p,action='get',id=agent['id'],include_instructions=True))
        self.assertEqual(saved['instructions'],instructions)
        self.assertEqual(saved['source'],'reviewer.TOML')
        self.assertEqual(saved['client'],'codex')
        with self.assertRaises(ValueError):run('agent_manage',dict(project=p,action='create',actor=self.human,name='Invalid',source='role.toml.exe',start_immediately=False))
