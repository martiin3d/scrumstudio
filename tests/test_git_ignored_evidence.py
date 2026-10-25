import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_low_turns as fixtures
from store import run
import git_sync
import git_completion

class IgnoredEvidenceTest(unittest.TestCase):
    setUp=fixtures.GitAutomationTest.setUp
    tearDown=fixtures.GitAutomationTest.tearDown
    project=fixtures.GitAutomationTest.project
    repo=fixtures.GitAutomationTest.repo
    enable=fixtures.GitAutomationTest.enable
    def fixture(self):
        p,root=self.project();git=self.repo(root)
        (root/'.gitignore').write_text('.scrum/\nLogs/\n');git('add','.gitignore');git('commit','-qm','Local evidence policy')
        self.enable(p,git_merge='Task',git_push='Never',usage_guard=False)
        task=run('task_create',dict(project=p,title='Verified output',files=['a.py','Logs/check.xml'],**fixtures.SPEC))['id']
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        (root/'a.py').write_text('verified source');(root/'Logs').mkdir();(root/'Logs/check.xml').write_text('<passed/>')
        return p,root,git,task,claim
    def test_ignored_evidence_is_kept_local_and_does_not_break_commit(self):
        p,root,git,task,claim=self.fixture()
        result=run('work_finish',dict(project=p,id=task,actor=self.agent,token=claim['token'],evidence='Actual fixture source and local report checked.'))
        self.assertEqual(result['completed']['status'],'Done')
        self.assertEqual(git('show','main:a.py'),'verified source');self.assertEqual(git('ls-files','Logs/check.xml'),'')
        self.assertEqual((root/'Logs/check.xml').read_text(),'<passed/>')
    def test_tracked_files_remain_publishable_when_a_later_ignore_rule_matches(self):
        p,root,git,task,claim=self.fixture()
        git('add','-f','Logs/check.xml');git('commit','-qm','Explicit prior tracked evidence')
        self.assertIn('Logs/check.xml',git_sync.planned_paths(root,['Logs/check.xml']))
    def legacy_recovery(self,changed=False):
        p,root,git,task,claim=self.fixture();branch=claim['git']['branch']
        with patch.object(git_sync,'_commit',return_value={'error':'Old ignored evidence git-add failure'}):
            failed=run('work_finish',dict(project=p,id=task,actor=self.agent,token=claim['token'],evidence='Preserved passing source and report'))
        recovery=failed['completed']['git']['recovery_task']['id']
        path=git_completion.location(root,task);receipt=json.loads(path.read_text())
        receipt['files']['Logs/check.xml']=git('hash-object','Logs/check.xml');git_completion.write_receipt(path,receipt)
        held=run('task_claim',dict(project=p,id=recovery,actor=self.agent))
        git('add','a.py');git('commit','-qm','Publish original verified source')
        merged=git_sync.merge_completed(root,run('project_settings',dict(project=p)),branch);self.assertTrue(merged['merged'])
        if changed:(root/'Logs/check.xml').write_text('<different/>')
        with patch.object(git_completion,'reconcile',return_value={'completed':[]}):
            run('work_finish',dict(project=p,id=recovery,actor=self.agent,token=held['token'],evidence='Original source integrated, retained verification unchanged.'))
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'In Review')
        run('worker_launch',dict(project=p,actor=self.agent))
        return p,root,task,path
    def test_completed_recovery_reconciles_original_at_native_launch(self):
        p,root,task,path=self.legacy_recovery()
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'Done')
        receipt=json.loads(path.read_text());self.assertIn('Logs/check.xml',receipt['local_evidence']);self.assertNotIn('Logs/check.xml',receipt['files'])
    def test_changed_local_evidence_is_not_silently_accepted(self):
        p,root,task,path=self.legacy_recovery(changed=True)
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'In Review')
