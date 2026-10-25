import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import run_cost
import test_app
from store import run
from mcp_server import handle


class RunCostTest(unittest.TestCase):
    def test_incremental_complete_events_and_once_only_advisory(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);log=root/'events';target=root/'cost.json';log.write_bytes(b'')
            clock=[0];watch=run_cost.Watch(log,0,target,'one',[1],{'run_advisory_tools':1},'codex',clock=lambda:clock[0])
            self.assertIsNone(run_cost.notice(target))
            event=json.dumps({'type':'item.completed','item':{'type':'command_execution','aggregated_output':'PRIVATE CONTENT'}}).encode()
            log.write_bytes(event[:15]);watch.tick();self.assertEqual(watch.state['tools'],0)
            log.write_bytes(event+b'\n');clock[0]=10;watch.tick();watch.tick()
            self.assertEqual(watch.state['tools'],1)
            self.assertIsNone(watch.state['input_tokens'])
            first=run_cost.notice(target);self.assertEqual(first['tools'],1)
            self.assertIsNone(run_cost.notice(target));self.assertNotIn('PRIVATE',target.read_text())
            watch.tick(active=False);self.assertIsNone(run_cost.notice(target))
            watch=run_cost.Watch(log,log.stat().st_size,target,'two',[2],{'run_advisory_minutes':1},'codex',clock=lambda:clock[0])
            clock[0]+=61;watch.tick();self.assertIsNotNone(run_cost.notice(target))

    def test_disabled_thresholds_missing_usage_and_terminal_counters(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            root=Path(folder);log=root/'events';target=root/'cost.json'
            log.write_text(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':90,'output_tokens':5}})+'\n')
            watch=run_cost.Watch(log,0,target,'one',[1],{k:0 for k in run_cost.DEFAULTS},'codex')
            self.assertEqual(watch.state['input_tokens'],100);self.assertIsNone(run_cost.notice(target))
            self.assertEqual(watch.state['cached_input_tokens'],90)
            value=json.loads(target.read_text());value.update(advisory=['test'],observed_at=1)
            target.write_text(json.dumps(value));self.assertIsNone(run_cost.notice(target))


class RunCostIntegrationTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def test_only_human_changes_policy_and_schema_accepts_it(self):
        human=run('user_register',{'identity':'human:cost','name':'Cost owner','kind':'human'})['id']
        result=handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'project_settings','arguments':{'project':self.p,'actor':human,'run_advisory_tools':12}}})['result']
        self.assertFalse(result.get('isError'),result)
        self.assertEqual(run('project_settings',{'project':self.p})['run_advisory_tools'],12)
        with self.assertRaises(ValueError):run('project_settings',{'project':self.p,'actor':self.actor,'run_advisory_tools':0})
        with self.assertRaises(ValueError):run('project_settings',{'project':self.p,'actor':human,'run_advisory_tools':-1})

    def test_existing_mcp_boundary_delivers_notice_once_without_extra_model(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data') as folder:
            path=Path(folder)/'cost.json'
            run_cost.atomic(path,dict(active=True,observed_at=run_cost.time.time(),run_id='one',advisory=['tools'],elapsed_seconds=10,tools=60,output_tokens=None))
            request={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'project_settings','arguments':{'project':self.p}}}
            with patch.dict('os.environ',{'SCRUM_RUN_COST_STATE':str(path)}):
                first=json.loads(handle(request)['result']['content'][0]['text'])
                second=json.loads(handle(request)['result']['content'][0]['text'])
            self.assertIn('efficiency_notice',first);self.assertNotIn('efficiency_notice',second)

    def test_paginated_file_plan_keeps_acceptance_continuity_and_all_paths(self):
        paths=[f'src/{n}.cs' for n in range(55)]
        task=run('task_create',dict(project=self.p,title='Large mechanical change',description='Preserve exact scope',acceptance_criteria=['All bindings valid'],files=paths))['id']
        run('task_update',dict(project=self.p,id=task,actor=self.actor,continuity='Done: imported. Next: bindings.'))
        first=run('task_get',dict(project=self.p,id=task))
        self.assertEqual(len(first['files']),20);self.assertEqual(first['files_page']['total'],55)
        self.assertEqual(first['acceptance_criteria'],['All bindings valid']);self.assertIn('Next: bindings',first['continuity'])
        collected=list(first['files'])
        for after in (20,40):collected+=run('task_get',dict(project=self.p,id=task,files_after=after))['files']
        self.assertEqual(collected,paths)
        self.assertEqual(run('task_get',dict(project=self.p,id=task,full=True))['files'],paths)
        self.assertFalse(run('task_get',dict(project=self.p,id=task,version_seen=first['version']))['changed'])
        with self.assertRaises(ValueError):run('task_get',dict(project=self.p,id=task,files_limit=0))
