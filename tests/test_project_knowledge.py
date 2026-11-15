import unittest
from unittest.mock import patch

from store import run
from test_storage import StorageTest
import project_knowledge


class ProjectKnowledgeTest(unittest.TestCase):
    setUp=StorageTest.setUp
    tearDown=StorageTest.tearDown
    project=StorageTest.project
    clone=StorageTest.clone

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

    def test_source_scope_and_spec_revision_reach_the_task(self):
        from context_tools import startup_task
        project,root=self.project()
        (root/'package.json').write_text('{"scripts":{"test":"node --test"}}')
        plan=self.knowledge(project,'prepare',answers=dict(goal='Goal',milestone='Outcome',acceptance='Observed check'))['plan']
        self.knowledge(project,'apply',plan_id=plan['plan_id'])
        (root/'design.md').write_text('Design source\n')
        (root/'code.js').write_text('first\n')
        page='docs/agents/project.md'
        self.knowledge(project,'ingest',source='design.md',page=page,text='Product decisions',scope=['code.js'])
        task=run('task_plan',dict(project=project,actor=self.human,items=[dict(key='candidate',title='Use specification',
                 description='Follow the first milestone',acceptance_criteria=['Verification'],files=['code.js'],
                 specification=dict(page=page,section='first-milestone'))]))['items'][0]
        lookup=dict(project=project,id=task['id'])
        claim=startup_task(run('task_get',lookup))
        self.assertEqual(claim['specification']['state'],'current')
        self.assertIn(page,claim['knowledge']['pages'])
        self.assertEqual(claim['knowledge']['verification'][0]['command'],'npm run test')
        run('task_update',dict(lookup,actor=self.human,continuity='Reuse the previous verified result'))
        (root/'code.js').write_text('second\n')
        self.assertEqual(run('task_get',lookup)['workspace_delta']['changed_paths'],['code.js'])
        spec=root/page
        spec.write_text(spec.read_text(encoding='utf-8').replace('Outcome','Changed outcome'),encoding='utf-8')
        self.assertEqual(run('task_get',lookup)['specification']['state'],'changed')
        (root/'design.md').write_text('Changed design\n')
        (root/'package.json').write_text('{}')
        changes=self.knowledge(project,'reassess')
        self.assertEqual(changes['references_changed'][0]['page'],page)
        self.assertNotIn('verification',run('task_get',lookup)['knowledge'])
        refreshed=self.knowledge(project,'refresh',plan_id=changes['plan_id'])
        self.assertIn('docs/agents/checks.md',refreshed['updated'])

    def test_section_refresh_preserves_manual_text_and_rejects_stale_preview(self):
        project,root=self.project()
        package=root/'package.json'
        package.write_text('{"scripts":{"test":"node --test"}}',encoding='utf-8')
        plan=self.knowledge(project,'prepare',answers=dict(goal='Goal',milestone='Outcome',acceptance='Check'))['plan']
        self.knowledge(project,'apply',plan_id=plan['plan_id'])
        wiki=root/'docs/agents'
        page=wiki/'checks.md'
        original=page.read_bytes()
        manual=b'\n## Maintainer notes\n\nPreserve this text.\n'
        page.write_bytes(original+manual)
        package.write_text('{"scripts":{"lint":"node --check app.js"}}',encoding='utf-8')
        preview=self.knowledge(project,'reassess')
        commands=next(item for item in preview['sections'] if item['section']=='Detected commands')
        self.assertIn('npm run test',commands['before'])
        self.assertIn('npm run lint',commands['after'])
        self.assertTrue(commands['applicable'])
        page.write_bytes(original+manual+b'Intervening edit.\n')
        with self.assertRaisesRegex(ValueError,'current source changes'):
            self.knowledge(project,'refresh',plan_id=preview['plan_id'])
        preview=self.knowledge(project,'reassess')
        package.write_text('{"scripts":{"build":"node build.js"}}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'current source changes'):
            self.knowledge(project,'refresh',plan_id=preview['plan_id'])
        preview=self.knowledge(project,'reassess')
        self.knowledge(project,'refresh',plan_id=preview['plan_id'])
        self.assertIn(b'npm run build',page.read_bytes())
        self.assertTrue(page.read_bytes().endswith(manual+b'Intervening edit.\n'))
        page.write_bytes(page.read_bytes().replace(b'npm run build',b'custom command'))
        package.write_text('{}',encoding='utf-8')
        preview=self.knowledge(project,'reassess')
        commands=next(item for item in preview['sections'] if item['section']=='Detected commands')
        self.assertFalse(commands['applicable'])
        result=self.knowledge(project,'refresh',plan_id=preview['plan_id'])
        self.assertIn('docs/agents/checks.md#Detected commands',result['retained_for_review'])
        self.assertIn(b'custom command',page.read_bytes())

    def test_read_signatures_distinguish_pagination_and_revision_changes(self):
        from overhead import task_read_signature
        def signature(version, **args):
            return task_read_signature('task_get',dict(id=1,**args),{'version':version})
        self.assertEqual(signature('current'),signature('current'))
        self.assertNotEqual(signature('current'),signature('current',files_after=20))
        self.assertNotEqual(signature('current'),signature('new'))

    def test_evidence_questions_and_capability_choices_survive_project_import(self):
        import shutil

        project,root=self.project()
        (root/'package.json').write_text('{"scripts":{"test":"node --test"}}')
        prepared=self.knowledge(project,'prepare',answers=dict(goal='Goal',milestone='Milestone',acceptance='Tests pass'))
        self.assertIn('Node.js',prepared['questions']['milestone'])
        self.assertIn('npm run test',prepared['questions']['acceptance'])
        result=self.knowledge(project,'apply',plan_id=prepared['plan']['plan_id'])
        review=self.knowledge(project,'reassess')
        self.knowledge(project,'capability',plan_id=review['plan_id'],capability='Node.js',decision='deferred')
        self.assertEqual(self.knowledge(project,'reassess')['capability_suggestions'],[])
        run('project_export',dict(project=project))
        opened,clone_root=self.clone(root)
        shutil.copytree(root/'docs',clone_root/'docs')
        other=opened['id']
        self.assertEqual(self.knowledge(other,'status')['phase'],'applied')
        self.assertEqual(self.knowledge(other,'reassess')['capability_decisions'],{'Node.js':'deferred'})
        task=run('task_get',dict(project=other,code=result['tasks'][0]['code']))
        self.assertEqual(task['specification']['state'],'current')
