import unittest
import test_app
from store import run


class InteractionTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown
    task=test_app.WorkflowTest.task

    def request(self,permission=False):
        self.human=run('user_register',{'identity':'human:test','name':'Human','kind':'human'})['id']
        run('agent_checkpoint',{'project':self.p,'actor':self.actor,'client':'codex','session_used_percent':1,'weekly_used_percent':1})
        self.t=self.task('Scoped work')
        self.claim=run('task_claim',{'project':self.p,'id':self.t,'actor':self.actor})
        return run('agent_interaction',{'project':self.p,'actor':self.actor,'action':'request','question':'Run this exact local check?','kind':'permission' if permission else 'question','options':['Approve once','Deny','Cancel run'] if permission else ['Continue','Wait']})

    def test_human_answers_once_and_agents_cannot_approve_themselves(self):
        q=self.request(True)
        self.assertNotIn('lease',q)
        with self.assertRaises(ValueError):run('agent_interaction',{'project':self.p,'actor':self.actor,'action':'answer','id':q['id'],'answer':'Approve once'})
        args={'project':self.p,'actor':self.human,'action':'answer','id':q['id'],'answer':'Approve once'}
        self.assertEqual(run('agent_interaction',args)['state'],'answered')
        self.assertTrue(run('agent_interaction',args)['already_answered'])
        with self.assertRaises(ValueError):run('agent_interaction',dict(args,answer='Deny'))

    def test_permission_answers_are_not_arbitrary_grants(self):
        q=self.request(True)
        with self.assertRaises(ValueError):run('agent_interaction',{'project':self.p,'actor':self.human,'action':'answer','id':q['id'],'answer':'Allow all folders forever'})

    def test_stopped_or_reclaimed_task_cannot_use_old_approval(self):
        q=self.request(True)
        run('task_release',{'project':self.p,'id':self.t,'actor':self.actor,'token':self.claim['token']})
        run('task_claim',{'project':self.p,'id':self.t,'actor':self.actor})
        with self.assertRaises(ValueError):run('agent_interaction',{'project':self.p,'actor':self.human,'action':'answer','id':q['id'],'answer':'Approve once'})
        self.assertEqual(run('agent_interaction',{'project':self.p})['items'][0]['state'],'expired')

    def test_question_free_text_project_isolation_and_conditional_reads(self):
        q=self.request()
        data=run('agent_interaction',{'project':self.p})
        self.assertFalse(run('agent_interaction',{'project':self.p,'version_seen':data['version']})['changed'])
        with self.assertRaises(ValueError):run('agent_interaction',{'project':self.q,'actor':self.human,'action':'answer','id':q['id'],'answer':'Continue'})
        run('agent_interaction',{'project':self.p,'actor':self.human,'action':'answer','id':q['id'],'answer':'Use the existing installed editor'})
        self.assertTrue(run('agent_interaction',{'project':self.p,'version_seen':data['version']})['changed'])

    def test_unclaimed_agent_cannot_create_permission_prompts(self):
        with self.assertRaises(ValueError):run('agent_interaction',{'project':self.p,'actor':self.actor,'action':'request','question':'Do it','options':[]})

    def test_project_stop_invalidates_permission_before_worker_cleanup(self):
        q=self.request(True)
        run('project_control',{'project':self.p,'actor':self.human,'action':'stop'})
        with self.assertRaises(ValueError):run('agent_interaction',{'project':self.p,'actor':self.human,'action':'answer','id':q['id'],'answer':'Approve once'})

    def test_stop_after_answer_still_prevents_native_callback_delivery(self):
        q=self.request(True)
        run('agent_interaction',{'project':self.p,'actor':self.human,'action':'answer','id':q['id'],'answer':'Approve once'})
        run('project_control',{'project':self.p,'actor':self.human,'action':'stop'})
        self.assertEqual(run('agent_interaction',{'project':self.p,'actor':self.actor,'action':'get','id':q['id']})['state'],'expired')
