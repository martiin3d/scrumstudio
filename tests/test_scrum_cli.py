import json
from pathlib import Path
from unittest.mock import patch
import unittest
import test_app
import scrum_cli as cli
from store import run

class NativeCliTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown
    task=test_app.WorkflowTest.task
    def context(self):
        path=Path(self.temp.name)/'private.json'
        cli.atomic(path,dict(schema=1,project=self.p,actor=self.actor,client='claude',batch=False,current=None,receipts=[]))
        return path
    def test_claim_finish_and_repeat_without_exposed_tokens(self):
        task=self.task('Native outcome');path=self.context()
        result=cli.coordinated('work_start',dict(id=task,claim=True,session_used_percent=1,weekly_used_percent=1),path)
        self.assertTrue(result['claim']['claimed']);self.assertNotIn('token',result['claim'])
        private=cli.context_file(path)['current']['token'];self.assertTrue(private)
        result=cli.coordinated('work_finish',dict(id=task,evidence='Targeted acceptance passed.'),path)
        self.assertEqual(result['completed']['status'],'Done');self.assertIsNone(cli.context_file(path)['current'])
        repeated=cli.coordinated('work_finish',dict(id=task,evidence='Already checked.'),path)
        self.assertTrue(repeated['replayed']);self.assertNotIn(private,json.dumps(repeated))
        import overhead
        metrics=overhead.metrics(self.p);self.assertEqual(metrics['totals']['calls'],2)
        self.assertEqual(metrics['channels'][0]['channel'],'cli')
    def test_interactive_start_bootstraps_private_context_for_task_code(self):
        import io
        task=self.task('Bootstrap named task')
        code=run('task_get',dict(project=self.p,id=task))['code']
        path=Path(self.temp.name)/'new-context.json';observed=Path(self.temp.name)/'usage.json'
        observed.write_text(json.dumps(dict(session_used_percent=1,weekly_used_percent=1)))
        out=io.StringIO()
        with patch('sys.stdout',out):
            status=cli.main(['start','--project',str(self.p),'--actor',str(self.actor),'--client','claude','--task',code,'--context',str(path),'--input',str(observed)])
        self.assertEqual(status,0);self.assertTrue(json.loads(out.getvalue())['claim']['claimed'])
        self.assertNotIn('"token"',out.getvalue());self.assertEqual(cli.context_file(path)['current']['id'],task)

    def test_uncertain_write_never_replayed(self):
        path=self.context();args=dict(id=self.task('Interrupted'),claim=True)
        with patch.object(cli,'invoke',side_effect=RuntimeError('transport unavailable')):
            with self.assertRaises(RuntimeError):cli.coordinated('work_start',args,path)
        with patch.object(cli,'invoke') as dispatch:
            with self.assertRaisesRegex(ValueError,'uncertain'):cli.coordinated('work_start',args,path)
            dispatch.assert_not_called()

    def test_incomplete_recovery_rejection_preserves_claim_without_uncertain_journal(self):
        from git_completion import RecoveryIncomplete
        task=self.task('Recovery validation');path=self.context()
        cli.coordinated('work_start',dict(id=task,claim=True,session_used_percent=1,weekly_used_percent=1),path)
        with patch.object(cli,'invoke',side_effect=RecoveryIncomplete('Original outputs are not committed')):
            with self.assertRaises(RecoveryIncomplete):cli.coordinated('work_finish',dict(id=task,evidence='Incomplete integration'),path)
        state=cli.context_file(path)
        self.assertNotIn('pending',state)
        self.assertEqual(state['current']['id'],task)
    def test_durable_result_repairs_context_without_repeating_write(self):
        path=self.context();state=cli.context_file(path)
        state['current']=dict(id=17,code='FIX-17',token='private-token');state['pending']=dict(key='fixture',operation='work_finish',task=17)
        cli.atomic(path,state);cli.atomic(str(path)+'.fixture.receipt',dict(operation='work_finish',args=dict(id=17),result=dict(completed=dict(id=17,status='Done'))))
        with patch.object(cli,'invoke') as dispatch:
            result=cli.coordinated('work_finish',dict(id=17,evidence='Already checked'),path)
            self.assertTrue(result['replayed']);dispatch.assert_not_called()
        self.assertIsNone(cli.context_file(path)['current']);self.assertFalse(Path(str(path)+'.fixture.receipt').exists())
    def test_overrides_and_done_bypass_rejected(self):
        path=self.context()
        with self.assertRaisesRegex(ValueError,'overridden'):cli.coordinated('task_get',dict(project=self.q,id=1),path)
        with self.assertRaisesRegex(ValueError,'Use finish'):cli.invoke('task_update',dict(project=self.p,id=1,status='Done'))
    def test_policy_round_trip(self):
        self.assertEqual(run('project_settings',dict(project=self.p))['coordination_channel'],'cli')
        human=run('user_register',dict(identity='human:cli',name='CLI owner',kind='human'))['id']
        self.assertEqual(run('project_settings',dict(project=self.p,actor=human,coordination_channel='cli'))['coordination_channel'],'cli')
        with self.assertRaises(ValueError):run('project_settings',dict(project=self.p,actor=human,coordination_channel='bad'))
    def test_channels_and_filters(self):
        import overhead
        for channel,task in [('mcp',11),('cli',12),('supervisor',13)]:
            with patch('overhead.time.time',return_value=1000):overhead.record(self.p,'task_get',10,20,2,False,task=task,channel=channel)
        result=overhead.metrics(self.p,start_at=900,end_at=1100)
        self.assertEqual(result['totals']['calls'],3);self.assertEqual({c['channel'] for c in result['channels']},{'mcp','cli','supervisor'})
        scoped=overhead.metrics(self.p,task=12,start_at=900,end_at=1100)
        self.assertEqual(scoped['channels'][0]['channel'],'cli');self.assertEqual(scoped['totals']['calls'],1)

    def test_rejected_plan_is_actionable_and_does_not_poison_journal(self):
        from planning import PlanRejected
        task=self.task('Current outcome');path=self.context()
        cli.coordinated('work_start',dict(id=task,claim=True,session_used_percent=1,weekly_used_percent=1),path)
        item=dict(key='prereq',title='New prerequisite',description='Concrete support needed',acceptance_criteria=['Support verified'])
        with self.assertRaisesRegex(PlanRejected,'unlocked'):
            cli.coordinated('task_plan',dict(items=[dict(item,id=task)]),path)
        self.assertNotIn('pending',cli.context_file(path))
        result=cli.coordinated('task_plan',dict(items=[item]),path)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(cli.context_file(path)['current']['id'],task)

    def test_plan_handoff_injects_private_token_and_clears_current_and_batch(self):
        task=self.task('Candidate validation');path=self.context()
        run('project_settings',dict(project=self.p,actor=run('user_register',dict(identity='human:cli-planning',name='Human',kind='human'))['id'],agent_followup_tasks=True))
        cli.coordinated('work_start',dict(id=task,claim=True,session_used_percent=1,weekly_used_percent=1),path)
        state=cli.context_file(path);private=state['current']['token'];state['batch']=True;cli.atomic(path,state)
        result=cli.coordinated('task_plan',dict(defer_task=task,prerequisites=['engine'],items=[dict(key='engine',title='Required engine operation',description='Implement missing operation',acceptance_criteria=['Operation checks pass'])]),path)
        self.assertTrue(result['deferred']);self.assertNotIn(private,json.dumps(result))
        state=cli.context_file(path);self.assertIsNone(state['current']);self.assertFalse(state['batch']);self.assertNotIn('pending',state)

    def test_uncertain_plan_still_blocks_writes(self):
        path=self.context()
        args=dict(items=[dict(key='outcome',title='Outcome',description='Concrete outcome',acceptance_criteria=['Verified'])])
        with patch.object(cli,'invoke',side_effect=RuntimeError('transport unavailable')):
            with self.assertRaises(RuntimeError):cli.coordinated('task_plan',args,path)
        with self.assertRaisesRegex(ValueError,'uncertain'):cli.coordinated('task_plan',args,path)

    def test_plan_rejection_message_is_visible_without_internal_error(self):
        import io
        from planning import PlanRejected
        err=io.StringIO()
        with patch.object(cli,'coordinated',side_effect=PlanRejected('Plan only unlocked unfinished tasks')),patch('sys.stderr',err),patch('sys.stdin',io.StringIO('{"items":[]}')):
            self.assertEqual(cli.main(['plan','--project',str(self.p),'--actor',str(self.actor),'--stdin']),1)
        result=json.loads(err.getvalue());self.assertEqual(result['error']['code'],'PLAN_REJECTED');self.assertFalse(result['error']['committed']);self.assertIn('unlocked',result['error']['message'])

    def test_plain_value_error_after_dispatch_keeps_uncertain_write_guard(self):
        path=self.context()
        args=dict(items=[dict(key='outcome',title='Outcome',description='Concrete outcome',acceptance_criteria=['Verified'])])
        with patch.object(cli,'invoke',side_effect=ValueError('Failure outside planning rollback boundary')):
            with self.assertRaises(ValueError):cli.coordinated('task_plan',args,path)
        self.assertIn('pending',cli.context_file(path))

    def test_finish_paths_batches_scope_without_an_extra_lock_operation(self):
        import io
        human=run('user_register',dict(identity='human:single-cli',name='Human',kind='human'))['id']
        run('project_settings',dict(project=self.p,actor=human,file_lock_policy='Auto',max_parallel_agents=1,workspace_root=str(Path(self.temp.name))))
        task=self.task('Single-worker CLI completion');path=self.context()
        started=cli.coordinated('work_start',dict(id=task,claim=True,session_used_percent=1,weekly_used_percent=1),path)
        self.assertEqual(started['claim']['file_lock_mode'],'checkout')
        out=io.StringIO()
        with patch('sys.stdout',out):
            result=cli.main(['finish','--context',str(path),'--task',str(task),'--paths','extra.py','--evidence','Targeted behavior passed.'])
        self.assertEqual(result,0);self.assertEqual(json.loads(out.getvalue())['completed']['status'],'Done')
        self.assertEqual(run('task_get',dict(project=self.p,id=task))['files'],['extra.py'])
        self.assertEqual(run('file_locks',dict(project=self.p))['items'],[])
        import overhead
        self.assertEqual(overhead.metrics(self.p)['totals']['calls'],2)
