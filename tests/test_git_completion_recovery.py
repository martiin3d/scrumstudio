"""Git recovery preserves verified intent before commit and closes the original."""
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import git_sync
import git_completion
from store import run
import test_low_turns as fixtures
SPEC=fixtures.SPEC


class CompletionRecoveryTest(unittest.TestCase):
    setUp=fixtures.GitAutomationTest.setUp
    tearDown=fixtures.GitAutomationTest.tearDown
    project=fixtures.GitAutomationTest.project
    repo=fixtures.GitAutomationTest.repo
    enable=fixtures.GitAutomationTest.enable

    def failed_commit(self):
        project,root=self.project();git=self.repo(root)
        self.enable(project,git_merge='Task',git_push='Never',usage_guard=False)
        task=run('task_create',dict(project=project,title='Verified source',files=['a.py'],**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));branch=claim['git']['branch']
        (root/'a.py').write_text('verified source')
        with patch.object(git_sync,'_commit',return_value={'error':'Simulated commit failure'}):
            result=run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Retained successful check'))
        self.assertEqual(result['completed']['status'],'In Review')
        receipt=json.loads(git_completion.location(root,task).read_text())
        self.assertIsNone(receipt['sha']);self.assertIn('a.py',receipt['files'])
        return project,root,git,task,branch,result['completed']['git']['recovery_task']['id']

    def recover(self,project,root,git,branch,recovery):
        claim=run('task_claim',dict(project=project,id=recovery,actor=self.agent))
        git('add','a.py');git('commit','-qm','Recover existing verified source')
        result=git_sync.merge_completed(root,run('project_settings',dict(project=project)),branch)
        self.assertTrue(result['merged'])
        return claim

    def test_directory_hint_does_not_expand_commit_scope(self):
        project,root=self.project();git=self.repo(root)
        self.enable(project,git_merge='Task',git_push='Never',usage_guard=False)
        files=['Fonts','Fonts/source.bin','Fonts/source.bin.meta']
        task=run('task_create',dict(project=project,title='Font source',files=files,**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
        (root/'Fonts').mkdir();(root/'Fonts/source.bin').write_bytes(b'\x00\xfffont')
        (root/'Fonts/source.bin.meta').write_bytes(b'metadata\r\n')
        (root/'Fonts/unrelated.txt').write_text('Unplanned work')
        result=run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified exact source files'))
        self.assertEqual(result['completed']['status'],'Done')
        self.assertIn('Fonts/source.bin',git('ls-tree','-r','--name-only','main'))
        self.assertNotIn('Fonts/unrelated.txt',git('ls-tree','-r','--name-only','main'))
        self.assertEqual((root/'Fonts/unrelated.txt').read_text(),'Unplanned work')

    def test_commit_failure_reconciles_after_recovery_and_publication_notes(self):
        project,root,git,task,branch,recovery=self.failed_commit()
        claim=self.recover(project,root,git,branch,recovery)
        run('task_update',dict(project=project,id=task,actor=self.agent,evidence='Same successful check; source now integrated',continuity='Only publication notes changed'))
        run('work_finish',dict(project=project,id=recovery,actor=self.agent,token=claim['token'],evidence='Verified source integrated'))
        self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'Done')

    def test_recovery_with_changed_unverified_bytes_does_not_complete_original(self):
        project,root,git,task,branch,recovery=self.failed_commit()
        (root/'a.py').write_text('unverified replacement')
        claim=self.recover(project,root,git,branch,recovery)
        run('work_finish',dict(project=project,id=recovery,actor=self.agent,token=claim['token'],evidence='Manual integration recorded'))
        self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'In Review')

    def test_changed_acceptance_does_not_reuse_old_intent(self):
        project,root,git,task,branch,recovery=self.failed_commit()
        claim=self.recover(project,root,git,branch,recovery)
        run('task_update',dict(project=project,id=task,actor=self.agent,acceptance_criteria=['A different outcome is required']))
        run('work_finish',dict(project=project,id=recovery,actor=self.agent,token=claim['token'],evidence='Existing source integrated'))
        self.assertEqual(run('task_get',dict(project=project,id=task))['status'],'In Review')

    def test_legacy_finish_from_another_active_branch_preserves_staging(self):
        project,root,git,task,branch,recovery=self.failed_commit()
        git_completion.location(root,task).unlink() # Reproduce receipts absent in old releases.
        claim=self.recover(project,root,git,branch,recovery)
        run('work_finish',dict(project=project,id=recovery,actor=self.agent,token=claim['token'],evidence='Existing source integrated'))
        other=run('task_create',dict(project=project,title='Other work',files=['b.py'],**SPEC))['id']
        run('task_claim',dict(project=project,id=other,actor=self.agent))
        (root/'b.py').write_text('other active edits');git('add','b.py')
        before=(git('branch','--show-current'),git('diff','--cached','--binary'),git('rev-parse','HEAD'))
        result=run('task_update',dict(project=project,id=task,actor=self.human,status='Done',evidence='Reused unchanged verified source and retained report'))
        self.assertEqual(result['status'],'Done')
        self.assertEqual(before,(git('branch','--show-current'),git('diff','--cached','--binary'),git('rev-parse','HEAD')))
        self.assertEqual((root/'b.py').read_text(),'other active edits')
        self.assertEqual(run('task_get',dict(project=project,id=other))['status'],'In Progress')
