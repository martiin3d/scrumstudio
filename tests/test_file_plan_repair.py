import json
from pathlib import Path
from unittest.mock import patch
import unittest
import test_worktrees as fixtures
from store import run
import file_plan_repair as repair
import coordination

class FilePlanRepairTest(unittest.TestCase):
    setUp=fixtures.WorktreeTest.setUp
    tearDown=fixtures.WorktreeTest.tearDown
    project=fixtures.WorktreeTest.project
    repo=fixtures.WorktreeTest.repo
    def fixture(self):
        p,root=self.project();self.repo(root)
        run('project_settings',dict(project=p,actor=self.human,git_mode='Branches',usage_guard=False,agent_followup_tasks=True))
        path='Artifacts/review';task=run('task_create',dict(project=p,title='Preserved work',files=[path],**fixtures.SPEC))
        claim=run('task_claim',dict(project=p,id=task['id'],actor=self.agent))
        (root/path).mkdir(parents=True);(root/path/'result.txt').write_text('preserved evidence')
        (root/path/'.gitignore').write_text('*.scratch\n');(root/path/'temp.scratch').write_text('ignored temporary')
        run('task_release',dict(project=p,id=task['id'],actor=self.agent,token=claim['token']))
        return p,root,task
    def test_idle_launch_repairs_placeholder_without_editing_or_staging(self):
        p,root,task=self.fixture()
        offer=run('worker_launch',dict(project=p,actor=self.agent))
        self.assertTrue(offer['eligible']);self.assertEqual(offer['id'],task['id'])
        files=run('task_get',dict(project=p,id=task['id'],full=True))['files']
        self.assertIn('Artifacts/review/result.txt',files);self.assertNotIn('Artifacts/review',files)
        self.assertNotIn('Artifacts/review/temp.scratch',files)
        self.assertEqual((root/'Artifacts/review/result.txt').read_text(),'preserved evidence')
    def test_no_opt_in_leaves_scope_unchanged(self):
        p,root,task=self.fixture()
        run('project_settings',dict(project=p,actor=self.human,agent_followup_tasks=False))
        run('worker_launch',dict(project=p,actor=self.agent))
        self.assertEqual(run('task_get',dict(project=p,id=task['id'],full=True))['files'],['Artifacts/review'])
    def test_explicit_children_do_not_broaden_scope(self):
        p,root,task=self.fixture()
        leaves=repair.expanded(root,['Artifacts/review','Artifacts/review/result.txt'])
        self.assertEqual(leaves,['Artifacts/review/result.txt'])
    def test_root_escape_and_large_directory_refused(self):
        p,root,task=self.fixture()
        for files in (['.'],['../escape']):
            with self.assertRaises(ValueError):repair.expanded(root,files)
        with patch.object(coordination,'MAX_PLANNED_FILES',1):
            with self.assertRaises(ValueError):repair.expanded(root,['Artifacts/review'])
    def test_foreign_live_claim_prevents_repair(self):
        p,root,task=self.fixture()
        other=run('user_register',dict(identity='agent:other-repair',name='Other',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=other,client='claude',session_used_percent=1,weekly_used_percent=1))
        another=run('task_create',dict(project=p,title='Current same branch',category='Git recovery',**fixtures.SPEC))
        claim=run('task_claim',dict(project=p,id=another['id'],actor=other));self.assertTrue(claim['claimed'])
        run('worker_launch',dict(project=p,actor=self.agent))
        self.assertEqual(run('task_get',dict(project=p,id=task['id'],full=True))['files'],['Artifacts/review'])
