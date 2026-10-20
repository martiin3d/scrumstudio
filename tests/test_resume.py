"""Stop/resume continuity: resumed sessions continue work instead of repeating it."""
import json
import sys
import unittest
from unittest.mock import patch
import test_app
from store import run
import mcp_server

SPEC=dict(description='Resumable outcome',acceptance_criteria=['Checked.'])

class ResumeTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def start(self,**extra):
        return run('work_start',dict(project=self.p,actor=self.actor,client='claude',session_used_percent=1,weekly_used_percent=1,**extra))

    def test_agent_recreating_known_title_reuses_task(self):
        first=run('task_create',dict(project=self.p,actor=self.actor,title='Add login page',**SPEC))
        again=run('task_create',dict(project=self.p,actor=self.actor,title='  add LOGIN   page ',**SPEC))
        self.assertEqual(again['id'],first['id']);self.assertTrue(again['existing'])
        separate=run('task_create',dict(project=self.p,actor=self.actor,title='Add login page',allow_duplicate=True,**SPEC))
        self.assertNotEqual(separate['id'],first['id'])
        human=run('task_create',dict(project=self.p,title='Add login page',**SPEC))
        self.assertNotIn('existing',human)

    def test_replanning_after_resume_maps_existing_outcomes(self):
        done=run('task_create',dict(project=self.p,actor=self.actor,title='Ship API',**SPEC))['id']
        plan=run('task_plan',dict(project=self.p,actor=self.actor,items=[dict(key='api',title='Ship API',**SPEC),dict(key='ui',title='Ship UI',dependencies=['api'],**SPEC)]))
        api,ui=plan['items']
        self.assertEqual((api['id'],api['existing']),(done,True))
        self.assertEqual(run('task_get',dict(project=self.p,id=ui['id']))['dependencies'],[done])
        self.assertEqual(len(run('snapshot',dict(project=self.p))['tasks']),2)

    def test_same_title_with_changed_scope_is_new_work(self):
        original=run('task_create',dict(project=self.p,actor=self.actor,title='Login',**SPEC))['id']
        token=run('task_claim',dict(project=self.p,id=original,actor=self.actor))['token']
        run('work_finish',dict(project=self.p,id=original,actor=self.actor,token=token,evidence='Checked.'))
        changed=dict(description='OAuth login',acceptance_criteria=['OAuth works'])
        other=run('task_create',dict(project=self.p,actor=self.actor,title='Login',**changed))
        self.assertNotEqual(other['id'],original)
        self.assertEqual(other['status'],'TODO')
        plan=run('task_plan',dict(project=self.p,actor=self.actor,items=[dict(key='new',title='Login',description='Passkey login',acceptance_criteria=['Passkeys work'])]))
        self.assertNotEqual(plan['items'][0]['id'],original)
        self.assertEqual(run('task_get',dict(project=self.p,id=original))['acceptance_criteria'],SPEC['acceptance_criteria'])

    def test_reworded_unfinished_work_is_reported_not_duplicated(self):
        original=run('task_create',dict(project=self.p,actor=self.actor,title='Login',files=['a.py'],**SPEC))['id']
        reworded=run('task_create',dict(project=self.p,actor=self.actor,title='login',description='Resumable outcome, reworded',acceptance_criteria=['Checked.'],files=['a.py','b.py']))
        self.assertEqual((reworded['id'],reworded['existing'],reworded['differs']),(original,True,['description','files']))
        self.assertIn('Nothing was created',reworded['note'])
        plan=run('task_plan',dict(project=self.p,actor=self.actor,items=[dict(key='l',title='Login',description='Other words',acceptance_criteria=['Checked.'])]))
        self.assertEqual((plan['items'][0]['id'],plan['items'][0]['differs']),(original,['description']))
        self.assertEqual(len(run('snapshot',dict(project=self.p))['tasks']),1)
        refined=run('task_plan',dict(project=self.p,actor=self.actor,items=[dict(key='l',id=original,title='Login',description='Other words',acceptance_criteria=['Checked.'])]))
        self.assertNotIn('existing',refined['items'][0]);self.assertEqual(run('task_get',dict(project=self.p,id=original))['description'],'Other words')

    def test_worker_keeps_planning_and_routing_fields(self):
        with patch.object(sys,'argv',['mcp_server.py','--profile','worker']):tools=mcp_server.catalog()
        for name in ('task_create','task_update'):
            properties=next(t for t in tools if t['name']==name)['inputSchema']['properties']
            self.assertTrue({'context_group','model_tier','model_reason','verification_level','verification_reason'}<=properties.keys())

    def test_paused_work_is_returned_first_and_listed_for_resume(self):
        other=run('task_create',dict(project=self.p,title='Other work',priority=1,**SPEC))['id']
        mine=run('task_create',dict(project=self.p,title='Half done',priority=3,**SPEC))['id']
        run('task_claim',dict(project=self.p,id=mine,actor=self.actor))
        run('agent_checkpoint',dict(project=self.p,actor=self.actor,client='claude',action='pause',summary='Edited a.py; next: run tests.'))
        started=self.start()
        self.assertEqual([t['id'] for t in started['resume']],[mine])
        self.assertIn('next: run tests',started['resume'][0]['continuity'])
        self.assertEqual(started['next']['task']['id'],mine)
        self.assertNotEqual(started['next']['task']['id'],other)

    def test_retried_finish_reports_already_done(self):
        task=run('task_create',dict(project=self.p,title='Finish once',**SPEC))['id']
        token=run('task_claim',dict(project=self.p,id=task,actor=self.actor))['token']
        args=dict(project=self.p,actor=self.actor,id=task,token=token,evidence='Checked.')
        run('work_finish',args)
        retry=run('work_finish',args)
        self.assertTrue(retry['completed']['already_done'])

    def test_worker_catalog_is_smaller_and_hides_admin_tools(self):
        request=dict(jsonrpc='2.0',id=1,method='tools/list')
        with patch.object(sys,'argv',['mcp_server.py']):full=mcp_server.handle(request)['result']['tools']
        with patch.object(sys,'argv',['mcp_server.py','--profile','worker']):worker=mcp_server.handle(request)['result']['tools']
        names={t['name'] for t in worker}
        self.assertIn('work_start',names);self.assertNotIn('project_migrate',names)
        self.assertLess(len(json.dumps(worker)),len(json.dumps(full))//2)
        for tool in worker:
            self.assertTrue(set(tool['inputSchema']['required'])<=set(tool['inputSchema']['properties']))
        # Hidden tools stay callable for bridges and scripts.
        call=dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='projects',arguments={}))
        with patch.object(sys,'argv',['mcp_server.py','--profile','worker']):
            self.assertFalse(mcp_server.handle(call)['result'].get('isError'))

if __name__=='__main__':unittest.main()
