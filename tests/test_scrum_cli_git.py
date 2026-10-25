import io
import json
import sys
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
import unittest
import test_low_turns as fixtures
import scrum_cli as cli
from store import run

class NativeCliGitTest(unittest.TestCase):
    setUp=fixtures.GitAutomationTest.setUp
    tearDown=fixtures.GitAutomationTest.tearDown
    project=fixtures.GitAutomationTest.project
    repo=fixtures.GitAutomationTest.repo
    enable=fixtures.GitAutomationTest.enable
    def test_native_cli_commits_pushes_and_merges_recorded_parent(self):
        project,root=self.project();git=self.repo(root)
        self.enable(project,git_merge='Task',git_push='Task',usage_guard=False,coordination_channel='cli')
        task=run('task_create',dict(project=project,title='CLI Git smoke',files=['a.py'],**fixtures.SPEC))
        path=root/'.scrum/local/test-cli.json'
        cli.atomic(path,dict(schema=1,project=project,actor=self.agent,client='claude',batch=False,current=None,receipts=[]))
        def command(*args):
            out=io.StringIO()
            with redirect_stdout(out):code=cli.main([*args,'--context',str(path)])
            self.assertEqual(code,0);self.assertNotIn('"token"',out.getvalue())
            return json.loads(out.getvalue())
        started=command('start','--task',task['code'])
        self.assertTrue(started['claim']['claimed']);branch=started['claim']['git']['branch']
        (root/'a.py').write_text('native CLI verified output\n')
        finished=command('finish','--task',task['code'],'--evidence','Actual fixture output checked before completion.')
        self.assertEqual(finished['completed']['status'],'Done')
        self.assertEqual(git('show','main:a.py'),'native CLI verified output')
        self.assertEqual(git('rev-parse','main'),git('rev-parse','origin/main'))
        self.assertEqual(git('rev-parse',branch),git('rev-parse','origin/'+branch))
        self.assertTrue(command('finish','--task',task['code'],'--evidence','Already verified')['replayed'])
    def test_stop_blocks_cli_claim_without_inference(self):
        project,root=self.project();self.enable(project,usage_guard=False)
        task=run('task_create',dict(project=project,title='Stopped scope',files=['a.py'],**fixtures.SPEC))
        run('project_control',dict(project=project,actor=self.human,action='stop',reason='Human Stop'))
        path=root/'.scrum/local/test-cli.json'
        cli.atomic(path,dict(schema=1,project=project,actor=self.agent,client='claude',batch=False,current=None,receipts=[]))
        result=cli.coordinated('work_start',dict(id=task['id'],claim=True),path)
        self.assertTrue(result['checkpoint']['should_stop'])
        self.assertIsNone(cli.context_file(path)['current'])
