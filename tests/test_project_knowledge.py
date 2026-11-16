import unittest
import json
from unittest.mock import Mock, patch

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

    def test_readiness_suggests_only_relevant_installed_skills_for_the_worker(self):
        project,root=self.project()
        home=root/'isolated-home'
        skills=root/'.codex/skills/playwright'
        skills.mkdir(parents=True)
        (skills/'SKILL.md').write_text('# Browser check',encoding='utf-8')
        run('task_create',dict(project=project,title='Refactor web preview',description='Change the web preview',
                               files=['public/app.js'],acceptance_criteria=['Verify the change in the browser.']))
        with patch('project_knowledge.Path.home',return_value=home),patch.dict('os.environ',{'CODEX_HOME':str(home/'.codex')}):
            codex=run('worker_readiness',dict(project=project,client='codex'))
            claude=run('worker_readiness',dict(project=project,client='claude'))
        self.assertEqual([item['name'] for item in codex['skill_suggestions']],['playwright'])
        self.assertEqual(codex['skill_suggestions'][0]['paths'],['public/app.js'])
        self.assertNotIn('skill_suggestions',claude)

    def test_graph_probe_uses_the_matching_checkout_and_is_bounded(self):
        project,root=self.project()
        args={'project':project,'paths':['store.py','workflow.py']}
        def response(body):
            return Mock(returncode=0,stderr='',stdout=json.dumps(body))
        backend='mcp_selection.subprocess.run'
        with patch('mcp_selection.shutil.which',return_value=None),patch(backend) as process:
            self.assertEqual(run('graph_readiness',args)['checks'][0]['state'],'missing')
            process.assert_not_called()
        with patch('mcp_selection.shutil.which',return_value='cbm'),patch(backend,return_value=Mock(returncode=1,stderr='endpoint')) as process:
            self.assertEqual(run('graph_readiness',args)['checks'][1]['state'],'blocked')
            process.assert_called_once()
        with patch('mcp_selection.shutil.which',return_value='cbm'),patch(backend,return_value=response({'projects':[]})):
            self.assertEqual(run('graph_readiness',args)['checks'][-1]['state'],'missing')
        with patch('mcp_selection.shutil.which',return_value='cbm'),patch(backend,side_effect=[
            response({'projects':[{'root_path':str(root.parent),'name':'wrong'},{'root_path':str(root),'name':'right'}]}),
            response({'paths':[{'requested_path':'store.py','status':'no_recorded_issue','freshness':'fresh'},
                               {'requested_path':'workflow.py','status':'no_recorded_issue','freshness':'metadata_changed'}]}),
        ]) as process:
            report=run('graph_readiness',args)
            payload=json.loads(process.call_args.kwargs['input'])
        self.assertEqual(report['indexed_project'],'right')
        self.assertEqual(report['checks'][-1]['state'],'stale')
        self.assertEqual(payload,{'project':'right','paths':['store.py','workflow.py'],'format':'json'})
        self.assertIn('rg',report['fallback'])
        with self.assertRaisesRegex(ValueError,'repository-relative'):
            run('graph_readiness',dict(args,paths=['../secret']))

    def test_worker_readiness_is_read_only_and_refreshes_changed_inputs(self):
        import mcp_selection
        project,root=self.project()
        path='docs/agents/contract.md'
        task_path=root/path
        task_path.parent.mkdir(parents=True)
        (task_path.parent/'index.md').write_text('[Contract](contract.md#scope)\n',encoding='utf-8')
        task_path.write_text('# Contract\n\n## Scope\n\nInitial behavior\n',encoding='utf-8')
        task=run('task_plan',dict(project=project,actor=self.human,items=[dict(
            key='continue',title='Continue scoped work',description='Implement the scope.',acceptance_criteria=['Tests pass'],
            files=[path],specification=dict(page=path,section='scope'))]))['items'][0]
        run('task_update',dict(project=project,id=task['id'],actor=self.human,continuity='Run verification.'))
        mcp_selection.save(root,'codex','selected',['initial-server'])
        args=dict(project=project,actor=self.agent,client='codex')
        before={file.relative_to(root).as_posix():file.read_bytes()
                for file in root.rglob('*') if file.is_file() and not file.name.endswith(('.db','.db-wal','.db-shm'))}
        with patch('mcp_selection.discover',return_value=({},[])),patch('scripts.cli_runtime.codex_executable',return_value='codex'):
            initial=run('worker_readiness',args)
            self.assertEqual(run('worker_readiness',dict(args,version_seen=initial['version'])),
                             {'changed':False,'version':initial['version']})
        after={file.relative_to(root).as_posix():file.read_bytes()
               for file in root.rglob('*') if file.is_file() and not file.name.endswith(('.db','.db-wal','.db-shm'))}
        self.assertEqual(before,after)
        self.assertEqual(initial['task']['id'],task['id'])
        checks={item['key']:item['state'] for item in initial['checks']}
        self.assertEqual(checks,{'runtime':'ready','tools':'missing','onboarding':'missing',
                                 'wiki':'ready','specification':'ready','verification':'unknown'})
        task_path.write_text('# Contract\n\n## Scope\n\nChanged behavior\n',encoding='utf-8')
        with patch('mcp_selection.discover',return_value=({'initial-server':{}},[])),patch('scripts.cli_runtime.codex_executable',return_value='codex'):
            changed=run('worker_readiness',dict(args,version_seen=initial['version']))
        self.assertTrue(changed['changed'])
        checks={item['key']:item for item in changed['checks']}
        self.assertEqual(checks['tools']['state'],'ready')
        self.assertEqual(checks['specification']['state'],'stale')
        self.assertIn('task pin',checks['specification']['next'])
        self.assertEqual(checks['verification']['state'],'stale')
        resulting=run('task_get',dict(project=project,id=task['id']))
        self.assertEqual(resulting['status'],'TODO')
        self.assertFalse(resulting['locked'])
        with patch('runtime.reason',return_value='Project stopped by human'):
            stopped=run('worker_readiness',args)
        self.assertEqual(stopped['state'],'blocked')
        self.assertEqual(stopped['task']['id'],task['id'])

    def test_specification_impact_is_section_scoped_and_paginated(self):
        project,root=self.project()
        relative='docs/agents/contract.md'
        path=root/relative
        path.parent.mkdir(parents=True)
        path.write_text('# Contract\n\n## Scope\n\nOld rule\n\n## Other\n\nUnchanged\n',encoding='utf-8')
        def task(key,section):
            return run('task_plan',dict(project=project,actor=self.human,items=[dict(
                key=key,title=key,description='Implement '+key,acceptance_criteria=['Verified'],
                specification=dict(page=relative,section=section))]))['items'][0]
        verified=task('scoped-check','scope')
        run('task_update',dict(project=project,id=verified['id'],actor=self.human,evidence='Original test passed.'))
        untouched=task('other-work','other')
        self.assertEqual(self.knowledge(project,'specification_impact')['groups'],[])
        path.write_text('# Contract\n\n## Scope\n\nNew rule\n\n## Other\n\nUnchanged\n',encoding='utf-8')
        legacy=task('missing-heading-check','scope')
        path.write_text('# Contract\n\n## Other\n\nUnchanged\n',encoding='utf-8')
        run('task_update',dict(project=project,id=legacy['id'],actor=self.human,evidence='No recorded scope.'))
        first=self.knowledge(project,'specification_impact',page=relative,section='scope',limit=1)
        self.assertTrue(first['has_more'])
        group=first['groups'][0]
        self.assertEqual(group['state'],'missing')
        self.assertEqual(group['tasks'][0]['id'],verified['id'])
        self.assertEqual(group['tasks'][0]['evidence']['state'],'requires_review')
        second=self.knowledge(project,'specification_impact',page=relative,after=first['next_after'],limit=1)
        self.assertFalse(second['has_more'])
        self.assertEqual(second['groups'][0]['tasks'][0]['evidence']['state'],'unknown')
        self.assertEqual(second['groups'][0]['tasks'][0]['id'],legacy['id'])
        self.assertNotIn(untouched['code'],str(first)+str(second))

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

    def test_source_summaries_are_reviewed_independently_and_bound_to_original_bytes(self):
        project,root=self.project()
        page='docs/agents/topic.md'
        topic=root/page
        topic.parent.mkdir(parents=True)
        (topic.parent/'index.md').write_text('[Topic](topic.md)\n',encoding='utf-8')
        topic.write_bytes(b'# Topic\r\n\r\nManual introduction.\r\n')
        runbook=root/'deployment.md'
        reference=root/'design.md'
        reference.write_text('Design reference.\n',encoding='utf-8')
        runbook.write_text('# Runbook\n\nDeploy the signed release.\n',encoding='utf-8')
        for source in (runbook,reference):
            self.knowledge(project,'ingest',source=source.name,page=page)
            preview=self.knowledge(project,'source_draft',source=source.name,page=page)
            self.assertIsNone(preview['accepted'])
            self.assertEqual(preview['path'],source.name)
            self.knowledge(project,'source_publish',source=source.name,page=page,plan_id=preview['plan_id'],
                           text='Reviewed '+source.stem+'.')
        edited='# Runbook\n\nDeploy with a canary first.\n'
        runbook.write_text(edited,encoding='utf-8')
        stale=self.knowledge(project,'reassess')['references_changed']
        self.assertEqual(stale,[{'path':'deployment.md','page':page,'change':'modified','section':'Summary of deployment.md'}])
        preview=self.knowledge(project,'source_draft',source=runbook.name,page=page)
        self.assertIn('Reviewed deployment.',preview['accepted'])
        self.assertIn('canary first',preview['draft'])
        topic.write_bytes(topic.read_bytes()+b'\r\nUnrelated tail.\r\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.knowledge(project,'source_publish',source=runbook.name,page=page,plan_id=preview['plan_id'],text='Reviewed canary.')
        preview=self.knowledge(project,'source_draft',source=runbook.name,page=page)
        self.knowledge(project,'source_publish',source=runbook.name,page=page,plan_id=preview['plan_id'],text='Reviewed canary.')
        body=topic.read_bytes()
        self.assertIn(b'Manual introduction.\r\n',body)
        self.assertIn(b'Reviewed design.\r\n',body)
        self.assertIn(b'Unrelated tail.\r\n',body)
        self.assertEqual(body.count(b'Reviewed canary.'),1)
        self.assertIn(preview['source_sha256'].encode(),body)
        self.assertEqual(runbook.read_text(encoding='utf-8'),edited)
        self.assertEqual(self.knowledge(project,'reassess')['references_changed'],[])

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
