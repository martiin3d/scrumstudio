import json
import unittest
import test_app
from store import run
from mcp_server import handle
import overhead


class EfficiencyTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def task(self,**extra):
        return run('task_create',dict(project=self.p,title='Scoped fix',description='One mechanical operation',acceptance_criteria=['Checked.'],**extra))['id']

    def rpc(self,name,args):
        response=handle(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name=name,arguments=args)))['result']
        self.assertFalse(response.get('isError'),response)
        return json.loads(response['content'][0]['text'])

    def test_scope_estimate_overrides_size_heuristic_only(self):
        task=self.task(files=['a'+str(i)+'.py' for i in range(6)])
        self.assertEqual(run('workflow_status',dict(project=self.p,id=task))['effective'],'Standard')
        with self.assertRaises(ValueError):run('task_update',dict(project=self.p,id=task,actor=self.actor,scope_size='Small'))
        run('task_update',dict(project=self.p,id=task,actor=self.actor,scope_size='Small',scope_reason='Same mechanical rename in six files, one regression.'))
        self.assertEqual(run('workflow_status',dict(project=self.p,id=task))['effective'],'Light')
        dep=self.task()
        run('task_update',dict(project=self.p,id=task,dependencies=[dep]))
        self.assertEqual(run('workflow_status',dict(project=self.p,id=task))['effective'],'Standard')

    def test_session_task_and_project_attribution(self):
        task=self.task()
        started=self.rpc('work_start',dict(project=self.p,actor=self.actor,client='codex',session_used_percent=1,weekly_used_percent=2))
        session=started['measurement_session']
        self.rpc('task_get',dict(project=self.p,id=task,measurement_session=session))
        result=run('coordination_metrics',dict(project=self.p,filter_session=session))
        self.assertEqual(result['totals']['calls'],2)
        task_result=run('coordination_metrics',dict(project=self.p,task=task,filter_session=session))
        self.assertEqual(task_result['totals']['calls'],1)
        self.assertEqual(run('coordination_metrics',dict(project=self.q))['totals']['calls'],0)
        self.assertIsNone(result['tokens'])
        self.assertEqual(result['sessions'][0]['actor'],self.actor)

    def test_conditional_response_is_smaller_without_losing_new_data(self):
        task=self.task()
        full=self.rpc('task_get',dict(project=self.p,id=task))
        small=self.rpc('task_get',dict(project=self.p,id=task,version_seen=full['version']))
        self.assertLess(len(json.dumps(small)),len(json.dumps(full)))
        run('task_update',dict(project=self.p,id=task,title='Changed title'))
        fresh=self.rpc('task_get',dict(project=self.p,id=task,version_seen=full['version']))
        self.assertEqual(fresh['title'],'Changed title')

    def test_unrelated_note_history_does_not_expand_resume(self):
        task=self.task()
        def startup():
            result=run('work_start',dict(project=self.p,actor=self.actor,client='codex',session_used_percent=1,weekly_used_percent=2))
            result.pop('measurement_session',None)
            return json.dumps(result,sort_keys=True)
        before=startup()
        other=self.task()
        for kind in ['Daily','Review','Retrospective']:
            run('note_add',dict(project=self.p,task=other,kind=kind,title='Unrelated history',items=['Long history '*250]))
        after=startup()
        self.assertNotIn('Long history',after)
        self.assertEqual(len(before),len(after))

    def test_agent_task_view_is_compact_and_selected_start_is_scoped(self):
        first=self.task()
        second=self.task()
        full=self.rpc('task_get',dict(project=self.p,id=second,full=True))
        compact=self.rpc('task_get',dict(project=self.p,id=second))
        self.assertLess(len(json.dumps(compact)),len(json.dumps(full)))
        self.assertNotIn('updated',compact)
        for key in ('description','acceptance_criteria','dependencies','checklist','files','status','evidence','locked','continuity'):
            self.assertEqual(compact[key],full[key])
        start=self.rpc('work_start',dict(project=self.p,actor=self.actor,client='codex',id=second,session_used_percent=1,weekly_used_percent=2))
        self.assertEqual(start['next']['task']['id'],second)
        self.assertNotIn(str(first)+' unrelated task',str(start))
        self.assertNotEqual(full['version'],compact['version'])

    def test_selection_is_small_and_resume_is_lossless(self):
        task=self.task()
        run('task_update',dict(project=self.p,id=task,actor=self.actor,
            description='Long scope '*400,acceptance_criteria=['Criterion '*100],
            checklist=[{'text':'Pending verification','done':False}],
            evidence='Previous check '*100,continuity='Remaining work '*100))
        run('task_update',dict(project=self.p,id=task,title='Changed after continuity'))
        detail=self.rpc('task_get',dict(project=self.p,id=task))
        header=self.rpc('next_work',dict(project=self.p,actor=self.actor))['task']
        for key in ('description','acceptance_criteria','checklist','evidence','continuity','attachments'):
            self.assertNotIn(key,header)
        self.assertLess(len(json.dumps(header)),len(json.dumps(detail))//5)
        self.assertNotEqual(header['version'],detail['version'])
        start=self.rpc('work_start',dict(project=self.p,actor=self.actor,client='codex',id=task,
            task_seen=header['version'],session_used_percent=1,weekly_used_percent=2))['next']['task']
        for key in ('description','acceptance_criteria','dependencies','blocked_reason','checklist','evidence','continuity','continuity_stale','files'):
            self.assertEqual(start[key],detail[key])
        self.assertTrue(start['continuity_stale'])
        self.assertEqual(detail['attachment_count'],0)
        self.assertNotIn('attachments',detail)

    def test_attachment_details_are_explicit(self):
        from context_tools import agent_task
        task={'id':1,'code':'SCR-1','attachments':[{'id':'ref','name':'reference.png'}]}
        detail=agent_task(task)
        self.assertEqual(detail['attachment_count'],1)
        self.assertNotIn('attachments',detail)
        self.assertEqual(task['attachments'][0]['name'],'reference.png')

    def test_compact_version_tracks_hidden_attachment_replacement(self):
        from context_tools import agent_task
        base={'id':1,'code':'SCR-1','attachments':[{'id':'one','name':'one.png'}]}
        first=agent_task(base|{'attachments':[{'id':'one','name':'one.png'}],'version':'source-one'})
        same=agent_task(base|{'attachments':[{'id':'one','name':'one.png'}],'version':'source-one'})
        replaced=agent_task(base|{'attachments':[{'id':'two','name':'two.png'}],'version':'source-two'})
        self.assertEqual(first['version'],same['version'])
        self.assertNotEqual(first['version'],replaced['version'])
        self.assertNotIn('attachments',replaced)
        self.assertEqual(replaced['attachment_count'],1)

    def test_search_and_selection_are_bounded_headers(self):
        task=self.task()
        run('task_update',dict(project=self.p,id=task,actor=self.actor,continuity='Secret continuation '*30,dependencies=[]))
        # Public operation exercises the same header projection as the API.
        results=self.rpc('task_search',dict(project=self.p))['items']
        self.assertNotIn('continuity_excerpt',results[0])
        self.assertNotIn('continuity',results[0])
        selection=self.rpc('next_work',dict(project=self.p,actor=self.actor))['task']
        self.assertIn('dependencies',selection)
        self.assertNotIn('description',selection)

    def test_conditional_compact_task_get_tracks_attachment_metadata(self):
        task=self.task()
        from store import connect
        metadata=lambda ident,name,sha:{'id':ident*32,'name':name,'mime':'text/plain','size':1,'sha256':sha*64,'created':'now','author':'tester','previewable':False}
        first=metadata('a','first.txt','b')
        db=connect()
        try:
            db.execute('UPDATE tasks SET attachments=? WHERE id=?',(json.dumps([first]),task))
            db.commit()
        finally:db.close()
        before=self.rpc('task_get',dict(project=self.p,id=task))
        unchanged=self.rpc('task_get',dict(project=self.p,id=task,version_seen=before['version']))
        self.assertFalse(unchanged['changed'])
        second=metadata('c','other.txt','d')
        db=connect()
        try:
            db.execute('UPDATE tasks SET attachments=? WHERE id=?',(json.dumps([second]),task))
            db.commit()
        finally:db.close()
        replaced=self.rpc('task_get',dict(project=self.p,id=task,version_seen=before['version']))
        self.assertNotIn('changed',replaced)
        self.assertNotEqual(before['version'],replaced['version'])
        self.assertEqual(replaced['attachment_count'],1)
        self.assertNotIn('attachments',replaced)

    def test_blocked_selection_omits_prose_reason(self):
        task=self.task()
        reason='Private detailed blocker. '*100
        run('task_update',dict(project=self.p,id=task,status='Blocked',blocked_reason=reason))
        result=self.rpc('next_work',dict(project=self.p,actor=self.actor))
        blocked=next(item for item in result['blocked'] if item['id']==task)
        self.assertEqual(blocked['availability'],'blocked_by_reason')
        self.assertEqual(blocked['status'],'Blocked')
        self.assertNotIn('blocked_reason',blocked)
        self.assertNotIn(reason,str(result))


class CostViewTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown
    def test_cost_opt_in_and_view_specific_versions(self):
        task=run('task_create',dict(project=self.p,actor=self.actor,title='Lazy cost',description='One fix',acceptance_criteria=['Verified'],estimated_tokens=100))['id']
        default=run('task_get',dict(project=self.p,id=task))
        self.assertNotIn('cost_summary',default)
        self.assertNotIn('cost_data',run('next_work',dict(project=self.p,actor=self.actor))['task'])
        self.assertNotIn('cost_data',run('snapshot',dict(project=self.p))['tasks'][0])
        cost=run('task_get',dict(project=self.p,id=task,include_cost=True,version_seen=default['version']))
        self.assertIn('cost_data',cost)
        self.assertNotEqual(cost['version'],default['version'])
        self.assertFalse(run('task_get',dict(project=self.p,id=task,include_cost=True,version_seen=cost['version']))['changed'])
