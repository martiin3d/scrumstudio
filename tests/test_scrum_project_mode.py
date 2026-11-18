import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_storage
from store import run


class ScrumProjectModeTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def control(self, project, action):
        return run('project_control',{'project':project,'actor':self.human,'action':action})['control']

    def test_off_is_local_and_start_skips_identity_and_quota(self):
        project,folder=self.project()
        other,_=self.project('Other')
        self.assertTrue(run('project_mode',{'project':project})['enabled'])
        with self.assertRaisesRegex(ValueError,'human'):
            run('project_control',{'project':project,'actor':self.agent,'action':'disable'})
        self.control(project,'disable')
        before=run('users',{})
        with patch('overhead.start_session') as measuring:
            info=run('work_start',{'project':project,'identity':'agent:unregistered','name':'Unregistered','client':'codex'})
        self.assertFalse(info['scrum_enabled'])
        self.assertTrue(info['checkpoint']['should_stop'])
        measuring.assert_not_called()
        self.assertEqual(before,run('users',{}))
        self.assertTrue(run('project_mode',{'project':other})['enabled'])
        cli=subprocess.check_output([sys.executable,'scrum.py','mode','--project',str(folder)],text=True)
        self.assertEqual(json.loads(cli),{'enabled':False,'pending_handoff':False})

    def test_active_claim_can_safely_handoff_after_off_and_enable_does_not_wake(self):
        project,_=self.project()
        task=run('task_create',{'project':project,'title':'Handoff','description':'Work for handoff','acceptance_criteria':['Evidence recorded']})['id']
        run('task_claim',{'project':project,'id':task,'actor':self.agent})
        self.assertTrue(run('agent_status',{'project':project})['execution_active'])
        self.control(project,'disable')
        self.assertTrue(run('project_mode',{'project':project})['pending_handoff'])
        self.assertTrue(run('task_get',{'project':project,'id':task})['locked'])
        report=run('agent_checkpoint',{'project':project,'actor':self.agent,'client':'codex','action':'pause','reason':'Safe pause','summary':'Resume after running the remaining check.'})
        self.assertEqual(report['tasks_released'],1)
        self.assertFalse(run('project_mode',{'project':project})['pending_handoff'])
        self.assertIn('remaining check',run('task_get',{'project':project,'id':task})['continuity'])
        with patch('managed_agents.launch') as launch:
            enabled=self.control(project,'enable')
        self.assertEqual((enabled['enabled'],enabled['paused']),(1,1))
        launch.assert_not_called()
        self.assertEqual(self.control(project,'enable')['generation'],enabled['generation'])
        self.assertEqual(run('project_control',{'project':project,'actor':self.human,'action':'wakeup','start_agents':False})['control']['paused'],0)

    def test_off_blocks_managed_launches_and_feature_requests(self):
        project,_=self.project()
        self.control(project,'disable')
        with patch('managed_agents.launch') as launch,patch('feature_planning.launch') as planner:
            with self.assertRaisesRegex(ValueError,'SCRUM is off'):
                self.control(project,'wakeup')
            with self.assertRaisesRegex(ValueError,'SCRUM is off'):
                run('agent_manage',{'project':project,'actor':self.human,'action':'create','name':'Agent'})
            with self.assertRaisesRegex(ValueError,'SCRUM is off'):
                run('feature_planning',{'project':project,'actor':self.human,'action':'submit','id':'f'*32,'text':'Plan a feature'})
            launch.assert_not_called()
            planner.assert_not_called()
