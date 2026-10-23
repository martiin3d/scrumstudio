import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import telemetry
from store import run

BASE=1700000000

class TelemetryTest(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'data'
        root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.previous=os.environ.get('SCRUM_DB')
        os.environ['SCRUM_DB']=str(Path(self.temp.name)/'timing.db')
        self.p=run('project_create',{'name':'Timing'})['id']
        self.q=run('project_create',{'name':'Other'})['id']
        self.actor=run('user_register',{'identity':'agent:timing','name':'Timing agent','kind':'agent'})['id']
        human=run('user_register',{'identity':'human:timing','name':'Human','kind':'human'})['id']
        run('project_settings',{'project':self.p,'actor':human,'workspace_root':str(Path(self.temp.name).resolve())})

    def test_worked_tasks_exclude_idle_terminal_state(self):
        from contextlib import closing
        import store
        active=self.task();idle=self.task()
        with closing(store.connect()) as db:
            db.execute('DELETE FROM task_states WHERE task IN (?,?)',(active,idle))
            db.execute('DELETE FROM task_activity WHERE task IN (?,?)',(active,idle))
            db.execute("INSERT INTO task_states(task,status,entered,exited,source) VALUES(?, 'In Progress', ?, ?, 'transition')",(active,BASE-100,BASE+100))
            db.execute("INSERT INTO task_states(task,status,entered,exited,source) VALUES(?, 'Done', ?, NULL, 'transition')",(idle,BASE-1000000))
            db.commit()
            result=telemetry.metrics(db,self.p,days=1,at=BASE)
        self.assertIn(active,result['worked_task_ids'])
        self.assertNotIn(idle,result['worked_task_ids'])

    def tearDown(self):
        if self.previous is None:
            os.environ.pop('SCRUM_DB',None)
        else:
            os.environ['SCRUM_DB']=self.previous
        self.temp.cleanup()

    def task(self, **kw):
        return run('task_create',dict(description='Test fixture: verify the named workflow outcome', acceptance_criteria=['The scenario assertions pass'], project=self.p,title='Task',actor=self.actor,allow_duplicate=True,**kw))['id']

    def claim(self,t,**kw):
        return run('task_claim',dict(project=self.p,id=t,actor=self.actor,**kw))['token']

    def update(self,t,**kw):
        return run('task_update',dict(project=self.p,id=t,actor=self.actor,**kw))

    def test_repeated_states_and_renewals_record_calendar_seconds(self):
        with patch('telemetry.time.time',return_value=BASE) as clock:
            t=self.task()
            clock.return_value=BASE+10
            token=self.claim(t)
            clock.return_value=BASE+12
            self.update(t,token=token,category='Backend')
            clock.return_value=BASE+15
            self.claim(t,token=token)
            clock.return_value=BASE+20
            run('task_release',{'project':self.p,'id':t,'actor':self.actor,'token':token})
            clock.return_value=BASE+21
            token=self.claim(t)
            clock.return_value=BASE+25
            self.update(t,token=token,status='Blocked',blocked_reason='Approval')
            clock.return_value=BASE+40
            self.update(t,status='TODO',blocked_reason='')
            clock.return_value=BASE+50
            token=self.claim(t)
            clock.return_value=BASE+90
            self.update(t,token=token,status='Done',evidence='Expected result verified.')
            clock.return_value=BASE+100
            timing=run('task_timing',{'project':self.p,'id':t,'history':True})
            self.assertFalse(timing['partial_history'])
            self.assertEqual(timing['seconds_by_state']['TODO'],20)
            self.assertEqual(timing['seconds_by_state']['In Progress'],55)
            self.assertEqual(timing['seconds_by_state']['Blocked'],15)
            self.assertEqual(timing['seconds_by_state']['Done'],10)
            self.assertEqual(len(timing['intervals']),6)
            page=run('task_timing',{'project':self.p,'id':t,'history':True,'limit':2})
            self.assertTrue(page['has_more'])
            older=run('task_timing',{'project':self.p,'id':t,'history':True,'before':page['oldest'],'limit':2})
            self.assertLess(older['intervals'][0]['id'],page['oldest'])

    def test_automatic_dependency_resolution_tracks_blocked_visit(self):
        with patch('telemetry.time.time',return_value=BASE) as clock:
            a,b=self.task(),self.task()
            clock.return_value=BASE+10
            self.update(b,status='Blocked',dependencies=[a])
            clock.return_value=BASE+30
            self.update(a,status='Done',evidence='Verified prerequisite.')
            clock.return_value=BASE+40
            result=run('task_timing',{'project':self.p,'id':b})
            self.assertEqual(result['current_state'],'TODO')
            self.assertEqual(result['seconds_by_state']['Blocked'],20)
            self.assertEqual(result['seconds_by_state']['TODO'],20)

    def test_cancel_releases_files_removes_blockers_and_can_reopen(self):
        with patch('telemetry.time.time',return_value=BASE) as clock:
            t=self.task(files=['edit.py'])
            dependent=self.task(dependencies=[t])
            self.update(dependent,status='Blocked')
            clock.return_value=BASE+10
            token=self.claim(t)
            with self.assertRaises(ValueError):
                self.update(t,token=token,status='Cancelled')
            clock.return_value=BASE+20
            self.update(t,token=token,status='Cancelled',cancel_reason='Scope removed by product owner.')
            self.assertEqual(run('file_locks',{'project':self.p})['items'],[])
            with self.assertRaises(ValueError):
                self.claim(t)
            self.assertEqual(run('next_work',{'project':self.p,'actor':self.actor})['task']['id'],dependent)
            blocked=run('task_get',{'project':self.p,'id':dependent})
            self.assertEqual(blocked['status'],'TODO')
            self.assertEqual(blocked['dependencies'],[])
            comments=run('task_activity',{'project':self.p,'id':dependent})['items']
            self.assertTrue(any('resolved by cancellation' in e['text'] and e['actor']==self.actor for e in comments))
            unrelated=self.task()
            with self.assertRaises(ValueError):
                self.update(unrelated,dependencies=[t])
            clock.return_value=BASE+50
            self.update(t,status='TODO')
            self.assertEqual(run('task_get',{'project':self.p,'id':t})['cancel_reason'],'')
            self.assertEqual(run('task_timing',{'project':self.p,'id':t})['seconds_by_state']['Cancelled'],30)

    def test_cancel_respects_remaining_dependencies_prose_and_other_projects(self):
        first,second=self.task(),self.task()
        dependent=self.task(dependencies=[first,second])
        prose=self.task(dependencies=[first])
        self.update(dependent,status='Blocked')
        self.update(prose,status='Blocked',blocked_reason='Human approval still required',blocked_reason_kind='external')
        foreign=run('task_create',{'description':'Test fixture: verify the named workflow outcome', 'acceptance_criteria':['The scenario assertions pass'], 'project':self.q,'title':'Untouched'})['id']
        self.update(first,status='Cancelled',cancel_reason='Discarded prerequisite')
        self.assertEqual(run('task_get',{'project':self.p,'id':dependent})['dependencies'],[second])
        self.assertEqual(run('task_get',{'project':self.p,'id':dependent})['status'],'Blocked')
        remaining=run('task_get',{'project':self.p,'id':prose})
        self.assertEqual(remaining['dependencies'],[])
        self.assertEqual(remaining['status'],'Blocked')
        self.update(second,status='Cancelled',cancel_reason='Also discarded')
        self.assertEqual(run('task_get',{'project':self.p,'id':dependent})['status'],'TODO')
        comments=run('task_activity',{'project':self.p,'id':dependent})['items']
        count=sum('resolved by cancellation' in e['text'] for e in comments)
        self.update(second,status='Cancelled')
        self.assertEqual(sum('resolved by cancellation' in e['text'] for e in run('task_activity',{'project':self.p,'id':dependent})['items']),count)
        self.assertEqual(run('task_get',{'project':self.q,'id':foreign})['status'],'TODO')
        self.assertEqual(run('project_metrics',{'project':self.p})['completed_tasks'],0)

    def test_metrics_recompletions_and_summary_series(self):
        with patch('telemetry.time.time',return_value=BASE) as clock:
            t=self.task()
            clock.return_value=BASE+10
            token=self.claim(t)
            clock.return_value=BASE+20
            self.update(t,token=token,status='Done',evidence='First verification passed.')
            clock.return_value=BASE+30
            self.update(t,status='TODO')
            clock.return_value=BASE+40
            token=self.claim(t)
            clock.return_value=BASE+50
            self.update(t,token=token,status='Done',evidence='Revised verification passed.')
            clock.return_value=BASE+60
            cancelled=self.task()
            clock.return_value=BASE+65
            self.update(cancelled,status='Cancelled',cancel_reason='No longer needed.')
            clock.return_value=BASE+70
            result=run('project_metrics',{'project':self.p,'days':7})
            self.assertNotIn('daily',result)
            self.assertEqual(result['distribution']['Done'],1)
            self.assertEqual(result['distribution']['Cancelled'],1)
            self.assertEqual(result['completed_tasks'],1)
            self.assertEqual(result['done_transitions'],2)
            self.assertEqual(result['cancelled_transitions'],1)
            self.assertEqual(result['average_lead_seconds'],50)
            self.assertEqual(result['average_cycle_seconds'],40)
            self.assertEqual(result['lead_samples'],1)
            chart=run('project_metrics',{'project':self.p,'days':7,'include_series':True})
            self.assertEqual(len(chart['daily']),7)
            self.assertEqual(sum(d['done'] for d in chart['daily']),2)
            self.assertEqual(sum(d['cancelled'] for d in chart['daily']),1)
            self.assertEqual(run('project_metrics',{'project':self.q})['total_tasks'],0)

    def test_legacy_baseline_is_partial_and_not_fabricated(self):
        t=self.task()
        with closing(sqlite3.connect(os.environ['SCRUM_DB'])) as db,db:
            db.execute('DELETE FROM task_states WHERE task=?',(t,))
        with patch('telemetry.time.time',return_value=BASE+200) as clock:
            initial=run('task_timing',{'project':self.p,'id':t})
            self.assertTrue(initial['partial_history'])
            self.assertEqual(initial['tracked_since'],BASE+200)
            clock.return_value=BASE+220
            token=self.claim(t)
            clock.return_value=BASE+250
            self.update(t,token=token,status='Done',evidence='Verified after tracking began.')
            result=run('project_metrics',{'project':self.p})
            self.assertEqual(result['legacy_tasks'],1)
            self.assertEqual(result['lead_samples'],0)
            self.assertEqual(result['cycle_samples'],0)
            self.assertEqual(run('task_timing',{'project':self.p,'id':t})['seconds_by_state']['TODO'],20)

    def test_metrics_mcp_and_project_isolation(self):
        from mcp_server import handle
        t=self.task()
        for name,args in [('task_timing',{'project':self.p,'id':t}),('project_metrics',{'project':self.p,'days':7})]:
            reply=handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})['result']
            self.assertNotIn('isError',reply)
            self.assertTrue(json.loads(reply['content'][0]['text']))
        with self.assertRaises(ValueError):
            run('task_timing',{'project':self.q,'id':t})
        for days in [0,366,True]:
            with self.assertRaises(ValueError):
                run('project_metrics',{'project':self.p,'days':days})
