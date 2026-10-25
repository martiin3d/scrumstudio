import unittest
from unittest.mock import patch
import test_app
from store import run
import scrum_cli
from pathlib import Path

class AutonomousTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown
    task=test_app.WorkflowTest.task
    def ready(self):
        self.human=run('user_register',dict(identity='human:autonomy',name='Owner',kind='human'))['id']
        run('project_settings',dict(project=self.p,actor=self.human,autonomy_mode='Autonomous'))
        run('agent_checkpoint',dict(project=self.p,actor=self.actor,client='claude',session_used_percent=1,weekly_used_percent=1))
        self.id=self.task('Specific interaction')
        self.claim=run('task_claim',dict(project=self.p,id=self.id,actor=self.actor))
    def ask(self,**kw):
        return run('agent_interaction',dict(project=self.p,actor=self.actor,task=self.id,action='request',question='Which of these two contradictory saved specifications is authoritative?',question_category='conflicting_requirements',human_requirement='Both user-provided requirements conflict; only their author can establish precedence.',options=['Use the current spec','Use the older exception'],**kw))
    def test_generic_questions_rejected_and_claim_retained(self):
        self.ready()
        with self.assertRaisesRegex(ValueError,'question_category'):run('agent_interaction',dict(project=self.p,actor=self.actor,action='request',question='Should I continue?',options=['Yes','No']))
        self.assertTrue(run('task_get',dict(project=self.p,id=self.id))['locked'])
    def test_specific_question_parks_only_its_task_and_human_answer_reopens(self):
        self.ready();other=self.task('Other independent work');q=self.ask()
        self.assertTrue(q['deferred']);self.assertNotIn('scope',q)
        task=run('task_get',dict(project=self.p,id=self.id));self.assertEqual(task['status'],'Blocked');self.assertFalse(task['locked'])
        self.assertEqual(run('next_work',dict(project=self.p,actor=self.actor))['task']['id'],other)
        answer=run('agent_interaction',dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Use the current spec'))
        self.assertTrue(answer['task_reopened']);task=run('task_get',dict(project=self.p,id=self.id));self.assertEqual(task['status'],'TODO');self.assertEqual(task['blocked_reason'],'')
        self.assertIn('Use the current spec',task['continuity']);self.assertNotEqual(task['status'],'Done')
    def test_stop_is_preserved_when_async_question_is_answered(self):
        self.ready();q=self.ask();run('project_control',dict(project=self.p,actor=self.human,action='stop'))
        generation=run('agent_status',dict(project=self.p))['control']['generation']
        run('agent_interaction',dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Use the current spec'))
        state=run('agent_status',dict(project=self.p));self.assertTrue(state['control']['paused']);self.assertEqual(state['control']['generation'],generation)
    def test_scope_change_invalidates_old_question(self):
        self.ready();q=self.ask()
        run('task_update',dict(project=self.p,id=self.id,actor=self.human,description='A materially different requirement'))
        with self.assertRaisesRegex(ValueError,'expired'):run('agent_interaction',dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Use the current spec'))
    def test_autonomous_permissions_still_require_real_human_and_live_lease(self):
        self.ready();q=run('agent_interaction',dict(project=self.p,actor=self.actor,action='request',kind='permission',question='Exact native command permission',options=['Approve once','Deny','Cancel run']))
        self.assertNotIn('deferred',q)
        with self.assertRaisesRegex(ValueError,'Only a human'):run('agent_interaction',dict(project=self.p,actor=self.actor,action='answer',id=q['id'],answer='Approve once'))
        run('task_release',dict(project=self.p,id=self.id,actor=self.actor,token=self.claim['token']))
        with self.assertRaisesRegex(ValueError,'expired'):run('agent_interaction',dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Approve once'))
    def test_cli_deferred_question_clears_claim_and_rejection_is_not_uncertain(self):
        self.ready();path=Path(self.temp.name)/'cli.json';task=run('task_get',dict(project=self.p,id=self.id))
        scrum_cli.create_context(path,self.p,self.actor,'claude',dict(task=task,token=self.claim['token']))
        with self.assertRaises(ValueError):scrum_cli.coordinated('agent_interaction',dict(action='request',kind='question',question='Continue?',options=['Yes','No']),path)
        self.assertNotIn('pending',scrum_cli.context_file(path));self.assertTrue(scrum_cli.context_file(path)['current'])
        result=scrum_cli.coordinated('agent_interaction',dict(action='request',kind='question',question='Which specification takes precedence?',question_category='conflicting_requirements',human_requirement='The two supplied specifications conflict and the author must choose their precedence.',options=['Current','Older']),path)
        self.assertTrue(result['deferred']);self.assertIsNone(scrum_cli.context_file(path)['current'])
    def test_answer_does_not_clear_a_separate_external_blocker(self):
        self.ready();q=self.ask()
        run('task_update',dict(project=self.p,id=self.id,actor=self.human,blocked_reason_kind='external',blocked_reason='The required external asset is still absent.'))
        reply=run('agent_interaction',dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Use the current spec'))
        self.assertNotIn('task_reopened',reply)
        task=run('task_get',dict(project=self.p,id=self.id));self.assertEqual(task['status'],'Blocked');self.assertIn('asset',task['blocked_reason'])
    def test_answer_is_idempotent_and_does_not_repeat_wakeup(self):
        self.ready();q=self.ask()
        payload=dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Use the current spec')
        run('agent_interaction',payload);generation=run('agent_status',dict(project=self.p))['control']['generation']
        self.assertTrue(run('agent_interaction',payload)['already_answered'])
        self.assertEqual(run('agent_status',dict(project=self.p))['control']['generation'],generation)
    def test_question_remains_answerable_after_claim_release_and_other_work(self):
        self.ready();q=self.ask();other=self.task('Parallel-independent work')
        claimed=run('task_claim',dict(project=self.p,id=other,actor=self.actor));self.assertTrue(claimed['claimed'])
        self.assertEqual(run('agent_interaction',dict(project=self.p,actor=self.actor,action='get',id=q['id']))['state'],'pending')
        run('agent_interaction',dict(project=self.p,actor=self.human,action='answer',id=q['id'],answer='Use the current spec'))
        self.assertTrue(run('task_get',dict(project=self.p,id=other))['locked'])

    def test_mode_is_human_only_and_validated(self):
        self.ready()
        with self.assertRaises(ValueError):run('project_settings',dict(project=self.p,actor=self.actor,autonomy_mode='Standard'))
        with self.assertRaises(ValueError):run('project_settings',dict(project=self.p,actor=self.human,autonomy_mode='Always approve'))
        self.assertEqual(run('project_settings',dict(project=self.p))['autonomy_mode'],'Autonomous')
    def test_invented_human_block_is_rejected(self):
        self.ready()
        with self.assertRaisesRegex(ValueError,'actual specific'):run('task_update',dict(project=self.p,id=self.id,actor=self.actor,token=self.claim['token'],status='Blocked',blocked_reason='Someone should approve this routine implementation.',blocked_reason_kind='human'))
