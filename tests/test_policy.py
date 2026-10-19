import json
import unittest
import test_storage
from store import run

class PolicyTest(unittest.TestCase):
    setUp = test_storage.StorageTest.setUp
    tearDown = test_storage.StorageTest.tearDown
    project = test_storage.StorageTest.project

    def test_defaults_permissions_and_validation(self):
        project,_=self.project()
        current=run('project_settings',{'project':project})
        self.assertEqual((current['task_sizing'],current['session_stop_percent'],current['weekly_stop_percent']),('Small',92,94))
        with self.assertRaises(ValueError):
            run('project_settings',{'project':project,'actor':self.agent,'session_stop_percent':99})
        for fields in [{'task_sizing':'huge'},{'session_stop_percent':0},{'weekly_stop_percent':101},{'usage_check_seconds':301}]:
            with self.assertRaises(ValueError):
                run('project_settings',dict(project=project,actor=self.human,**fields))
        updated=run('project_settings',{'project':project,'actor':self.human,'task_sizing':'Balanced','session_stop_percent':90,'weekly_stop_percent':93})
        self.assertGreater(updated['settings_version'],current['settings_version'])

    def test_snapshot_new_policy_and_old_defaults(self):
        project,path=self.project()
        run('project_settings',{'project':project,'actor':self.human,'task_sizing':'Balanced','session_stop_percent':88})
        run('project_export',{'project':project})
        file=path/'.scrum/snapshot.json'
        data=json.loads(file.read_text())
        self.assertEqual(data['settings']['session_stop_percent'],88)
        data['settings']={key:data['settings'][key] for key in ['max_active_per_agent','require_evidence']}
        file.write_text(json.dumps(data))
        run('project_restore',{'project':project,'confirm':True})
        self.assertEqual(run('project_settings',{'project':project})['task_sizing'],'Small')
