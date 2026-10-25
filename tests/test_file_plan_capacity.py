import json
import unittest
import test_storage
from store import run
from mcp_server import handle
from scripts.mcp_client import ScrumClient
import test_low_turns


class FilePlanCapacityTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    repo=test_low_turns.GitAutomationTest.repo

    def test_real_mcp_incremental_plan_preserves_205_paths_and_conflicts(self):
        p,root=self.project()
        paths=[f'planned/file-{i}.txt' for i in range(205)]
        task=run('task_create',dict(project=p,title='Complete migration',description='One cohesive migration',acceptance_criteria=['Complete explicit plan'],files=[]))['id']
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        c=ScrumClient(timeout=15)
        try:
            for offset in range(0,205,60):
                result=c.call('file_locks',dict(project=p,id=task,actor=self.agent,token=claim['token'],action='acquire',paths=paths[offset:offset+60]))
                self.assertTrue(result['acquired'])
            self.assertEqual(set(c.call('task_get',dict(project=p,id=task,full=True))['files']),set(paths))
        finally:c.close()
        locks=run('file_locks',dict(project=p,limit=100))
        self.assertTrue(locks['has_more'])
        collected=list(locks['items'])
        while locks['has_more']:
            locks=run('file_locks',dict(project=p,limit=100,after=locks['next_cursor']))
            collected.extend(locks['items'])
        self.assertEqual(len(collected),205)
        # Reacquisition near the limit must deduplicate before total-plan validation.
        run('file_locks',dict(project=p,id=task,actor=self.agent,token=claim['token'],action='acquire',paths=paths))
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],files=paths))
        before=run('task_get',dict(project=p,id=task,full=True))['files']
        with self.assertRaises(ValueError):run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],files=[f'{i}.txt' for i in range(501)]))
        self.assertEqual(run('task_get',dict(project=p,id=task,full=True))['files'],before)
        other=run('user_register',dict(identity='agent:capacity-other',name='Other',kind='agent'))['id']
        run('agent_checkpoint',dict(project=p,actor=other,client='codex',session_used_percent=1,weekly_used_percent=1))
        competing=run('task_create',dict(project=p,title='Competing task',description='Must respect reservations',acceptance_criteria=['Reserved files protected'],files=[paths[0]]))['id']
        self.assertFalse(run('task_claim',dict(project=p,id=competing,actor=other))['claimed'])
        tools=handle(dict(jsonrpc='2.0',id=1,method='tools/list'))['result']['tools']
        schema=next(t for t in tools if t['name']=='file_locks')['inputSchema']['properties']['paths']
        self.assertEqual(schema['maxItems'],500)

    def test_complete_large_plan_publishes_without_command_line_overflow(self):
        p,root=self.project();git=self.repo(root)
        run('project_settings',dict(project=p,actor=self.human,git_mode='Branches',git_merge='Task',git_push='Task',usage_guard=False))
        # The combined path list exceeds Windows' process command line limit.
        paths=[('folder-'+str(i).zfill(3)+'-'+'x'*90)+'/item.txt' for i in range(205)]
        task=run('task_create',dict(project=p,title='Large cohesive migration',description='One atomic file migration',acceptance_criteria=['Published complete plan'],files=paths))['id']
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        for name in paths:
            path=root/name;path.parent.mkdir();path.write_text('verified content')
        result=run('work_finish',dict(project=p,id=task,actor=self.agent,token=claim['token'],evidence='All explicit fixture paths verified'))
        self.assertEqual(result['completed']['status'],'Done')
        self.assertTrue(set(paths).issubset(set(git('ls-tree','-r','--name-only','main').splitlines())))
        self.assertEqual(git('rev-parse','main'),git('rev-parse','refs/remotes/origin/main'))

        import git_paused,git_sync
        for name in paths:(root/name).write_text('preserved partial')
        branch=git('branch','--show-current')
        with git_sync.checkout_lock(root):
            checkpoint=git_paused.pause(root,branch,paths,['FIX-1'])
        self.assertEqual(checkpoint['files'],205)
        self.assertTrue(all((root/name).read_text()=='verified content' for name in paths))
        restored=git_paused.restore(root,branch,'FIX-1')
        self.assertTrue(restored)
        self.assertTrue(all((root/name).read_text()=='preserved partial' for name in paths))
