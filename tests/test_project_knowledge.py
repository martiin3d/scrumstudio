import unittest
from unittest.mock import patch

from store import run
from test_storage import StorageTest
import project_knowledge


class ProjectKnowledgeTest(unittest.TestCase):
    setUp=StorageTest.setUp
    tearDown=StorageTest.tearDown
    project=StorageTest.project

    def knowledge(self, project, action, **args):
        return run('project_knowledge',dict(project=project,actor=self.human,action=action,**args))

    def test_empty_folder_persists_answers_and_applies_once(self):
        project,root=self.project()
        first=self.knowledge(project,'prepare')['plan']
        self.assertEqual(first['inventory']['kind'],'empty')
        self.assertFalse(first['ready'])
        self.assertFalse((root/'docs').exists())
        answers=dict(goal='Customers can browse a catalog.',milestone='Show the first product.',acceptance='The product is visible.')
        ready=self.knowledge(project,'prepare',answers=answers)['plan']
        self.assertEqual(self.knowledge(project,'status')['plan'],ready)
        result=self.knowledge(project,'apply',plan_id=ready['plan_id'])
        self.assertEqual(self.knowledge(project,'apply',plan_id=ready['plan_id']),result)
        self.assertEqual(len(run('task_search',dict(project=project,query='Show the first product'))['items']),1)
        self.assertIn(answers['goal'],self.knowledge(project,'view',page='docs/agents/project.md')['body'])
        self.assertEqual(self.knowledge(project,'diagnose')['issues'],[])

    def test_existing_folder_uses_sources_and_resumes_partially_written_plan(self):
        project,root=self.project()
        (root/'package.json').write_text('{"scripts":{"test":"node --test"}}')
        (root/'README.md').write_text('# Example')
        answers=dict(goal='Keep the API working.',milestone='Add a health check.',acceptance='Health returns 200.')
        plan=self.knowledge(project,'prepare',answers=answers)['plan']
        self.assertEqual(plan['inventory']['kind'],'existing')
        self.assertIn('npm run test',plan['pages']['docs/agents/checks.md'])
        write=project_knowledge.write_wiki
        def interrupt(root,path,body):
            if path.name=='architecture.md':raise OSError('interrupted')
            return write(root,path,body)
        with patch.object(project_knowledge,'write_wiki',side_effect=interrupt):
            with self.assertRaisesRegex(OSError,'interrupted'):
                self.knowledge(project,'apply',plan_id=plan['plan_id'])
        status=self.knowledge(project,'status')
        self.assertEqual(status['phase'],'applying')
        self.assertEqual(status['plan']['plan_id'],plan['plan_id'])
        self.assertEqual(self.knowledge(project,'apply',plan_id=plan['plan_id'])['phase'],'applied')

    def test_changed_scan_requires_fresh_prepared_plan(self):
        project,root=self.project()
        plan=self.knowledge(project,'prepare',answers=dict(goal='Goal',milestone='Outcome',acceptance='Observed check'))['plan']
        (root/'package.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'sources changed'):
            self.knowledge(project,'apply',plan_id=plan['plan_id'])
        self.assertFalse((root/'docs').exists())
