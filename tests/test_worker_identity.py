from contextlib import closing, contextmanager, redirect_stdout
import io
import os
import unittest
from unittest.mock import patch

import managed_agents
import scrum_cli
from scripts import claude_hooks
from store import run
import test_storage
from worker_identity import bridge_worker, machine_scope, standalone_worker


@contextmanager
def machine(number):
    with patch('worker_identity._host_identifier', return_value=f'{number:032x}'):
        machine_scope.cache_clear()
        try:
            yield
        finally:
            machine_scope.cache_clear()


class WorkerIdentityTest(unittest.TestCase):
    setUp = test_storage.StorageTest.setUp
    tearDown = test_storage.StorageTest.tearDown
    project = test_storage.StorageTest.project
    clone = test_storage.StorageTest.clone

    def task(self, project, title):
        return run('task_create', dict(
            project=project, title=title, description=title,
            acceptance_criteria=['Worker ownership is preserved'],
        ))['id']

    def observe(self, project, actor, client, **extra):
        return run('agent_checkpoint', dict(
            project=project, actor=actor, client=client,
            session_used_percent=1, weekly_used_percent=1, **extra,
        ))

    def test_machine_and_worker_names_are_stable_and_distinct(self):
        for client in ('codex', 'claude'):
            with machine(1):
                first = bridge_worker('copied-checkout', client, 'primary')
                second = bridge_worker('copied-checkout', client, 'reviewer')
            with machine(1):
                self.assertEqual(first, bridge_worker('copied-checkout', client, 'primary'))
            with machine(2):
                remote = bridge_worker('copied-checkout', client, 'primary')
            for field in ('identity', 'name'):
                self.assertEqual(len({first[field], second[field], remote[field]}), 3)

    def test_matching_remote_worker_cannot_use_live_claim_and_copy_has_no_lease(self):
        project, folder = self.project()
        checkout = next(p['checkout'] for p in run('projects', {}) if p['id'] == project)
        for client in ('codex', 'claude'):
            task = self.task(project, f'{client} claim')
            with machine(1):
                original = bridge_worker(checkout, client, 'primary')
                owner = run('user_register', original)['id']
                local_scope = machine_scope()
            self.observe(project, owner, client)
            claim = run('task_claim', dict(project=project, actor=owner, id=task))
            with machine(2):
                remote = run('user_register', bridge_worker(checkout, client, 'primary'))['id']
            self.observe(project, remote, client)
            resume = run('session_resume', dict(project=project, actor=remote))
            self.assertFalse(resume['resume'])
            args = dict(project=project, actor=remote, id=task, token=claim['token'])
            for action in ('task_claim', 'task_release', 'work_finish'):
                extra = {'evidence': 'Checked'} if action == 'work_finish' else {}
                with self.assertRaises(ValueError, msg=action):
                    run(action, args | extra)
            self.assertEqual(run('task_get', dict(project=project, id=task))['owner_id'], owner)
            run('project_export', dict(project=project))
            snapshot = (folder / '.scrum/snapshot.json').read_text(encoding='utf-8')
            self.assertIn(original['identity'], snapshot)
            self.assertNotIn(local_scope, snapshot)
            self.assertNotIn(claim['token'], snapshot)
            with patch.dict(os.environ, {'SCRUM_REGISTRY': str(self.root / f'{client}' / 'registry.db')}):
                copied, _ = self.clone(folder)
                copy_tasks = run('snapshot', {'project': copied['id']})['tasks']
                self.assertFalse(any(t['locked'] for t in copy_tasks))
                with machine(2):
                    alias = bridge_worker(copied['checkout'], client, 'primary')
                    local_user = run('user_register', alias)
                historical = next(u for u in run('users', {}) if u['identity'] == original['identity'])
                self.assertNotEqual(local_user['id'], historical['id'])
                self.assertNotEqual(local_user['name'], historical['name'])
            run('task_release', dict(project=project, actor=owner, id=task, token=claim['token']))

    def test_managed_upgrade_keeps_legacy_claim_and_handoff_continuity(self):
        project, folder = self.project()
        checkout = next(p['checkout'] for p in run('projects', {}) if p['id'] == project)
        with machine(1):
            profile = run('agent_manage', dict(
                project=project, actor=self.human, action='create', name='Builder', start_immediately=False,
            ))
            old = run('user_register', dict(
                identity=f"agent:managed:{checkout}:{profile['id']}", name='Builder', kind='agent',
            ))['id']
            with closing(managed_agents.connect(folder)) as db, db:
                db.execute('UPDATE profiles SET actor=?,desired=1 WHERE id=?', (old, profile['id']))
            task = self.task(project, 'Existing work')
            self.observe(project, old, 'codex', profile_id=profile['id'], action='observe', running=True)
            claim = run('task_claim', dict(project=project, actor=old, id=task))
            context = folder / 'legacy-context.json'
            scrum_cli.atomic(context, dict(
                schema=1, project=project, actor=old, client='codex', batch=False,
                current={'id': task, 'code': claim['code'], 'token': claim['token']}, receipts=[],
            ))
            start = dict(project=project, actor=self.human, action='start', id=profile['id'])
            with patch('managed_agents.subprocess.Popen') as spawn:
                self.assertEqual(run('agent_manage', start)['actor'], old)
                spawn.assert_not_called()
            self.assertTrue(run('task_get', dict(project=project, id=task))['locked'])
            scrum_cli.coordinated('agent_checkpoint', dict(action='offline', summary='Resume the saved check.'), context)
            with patch('managed_agents.codex_executable', return_value='codex'), patch('managed_agents.subprocess.Popen'):
                upgraded = run('agent_manage', start)
            self.assertEqual(upgraded['actor'], profile['actor'])
            self.assertNotEqual(upgraded['actor'], old)
            self.observe(project, upgraded['actor'], 'codex')
            resumed = run('work_start', dict(
                project=project, actor=upgraded['actor'], client='codex', id=task, claim=True,
            ))
            self.assertTrue(resumed['claim']['claimed'])
            self.assertIn('Resume the saved check.', resumed['task']['continuity'])

    def test_new_native_context_uses_local_worker_and_rejects_copy(self):
        project, folder = self.project()
        path = folder / 'worker-context.json'
        with machine(1), redirect_stdout(io.StringIO()):
            self.assertEqual(scrum_cli.main([
                'start', '--project', str(project), '--client', 'claude', '--context', str(path),
            ]), 0)
            actor = scrum_cli.context_file(path)['actor']
            self.assertEqual(actor, run('user_register', standalone_worker('claude'))['id'])
        with machine(1):
            self.assertEqual(scrum_cli.context_file(path)['actor'], actor)
        with machine(2):
            with self.assertRaisesRegex(ValueError, 'different machine'):
                scrum_cli.context_file(path)

    def test_claude_hooks_resolve_the_local_standalone_worker(self):
        project, folder = self.project()
        with machine(1):
            _, matched, first = claude_hooks.board(folder)
            self.assertEqual(matched['id'], project)
            self.assertEqual(first, run('user_register', standalone_worker('claude'))['id'])
        with machine(1):
            self.assertEqual(first, claude_hooks.board(folder)[2])
        with machine(2):
            self.assertNotEqual(first, claude_hooks.board(folder)[2])
