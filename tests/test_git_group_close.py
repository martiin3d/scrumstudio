"""Completed Group deliveries must not become unrelated tasks' origins."""
import json
import subprocess
import unittest
from unittest.mock import patch

import git_sync as git
import mcp_server
import scrum_cli
import test_low_turns as fixtures
from store import run


class GroupCheckoutTest(unittest.TestCase):
    setUp=fixtures.GitAutomationTest.setUp
    tearDown=fixtures.GitAutomationTest.tearDown
    project=fixtures.GitAutomationTest.project
    repo=fixtures.GitAutomationTest.repo
    enable=fixtures.GitAutomationTest.enable

    def setup_repo(self):
        project,root=self.project();g=self.repo(root)
        self.enable(project,git_merge='Group',git_commit='Task',git_push='Group',usage_guard=False)
        run('project_settings',dict(project=project,actor=self.human,git_base_branch=''))
        return project,root,g

    def task(self,project,title,path,group=''):
        return run('task_create',dict(project=project,title=title,files=[path],context_group=group,**fixtures.SPEC))['id']

    def finish(self,project,task,claim):
        return run('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified fixture outcome'))['completed']['git']

    def test_consecutive_independent_tasks_publish_to_main_through_cli_and_mcp(self):
        for channel in ('cli','mcp'):
            with self.subTest(channel=channel):
                project,root,g=self.setup_repo()
                def dispatch(name,args):
                    if channel=='cli':return scrum_cli.invoke(name,args)
                    result=mcp_server.handle(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name=name,arguments=args)))['result']
                    self.assertFalse(result.get('isError'),result)
                    return json.loads(result['content'][0]['text'])
                for title,path in [('First','a.py'),('Second','b.py')]:
                    task=self.task(project,title,path)
                    claim=dispatch('task_claim',dict(project=project,id=task,actor=self.agent))
                    self.assertEqual(git.parent_branch(root,claim['git']['branch']),'main')
                    (root/path).write_text(title)
                    done=dispatch('work_finish',dict(project=project,id=task,actor=self.agent,token=claim['token'],evidence='Verified '+title))
                    report=done['completed']['git']
                    self.assertTrue(report['checkout']['closed'])
                    self.assertEqual(git.state(root),('main',[]))
                    self.assertEqual(g('rev-parse','main'),g('ls-remote','origin','refs/heads/main').split()[0])
                self.assertEqual(g('show','main:a.py'),'First')
                self.assertEqual(g('show','main:b.py'),'Second')

    def test_human_approval_after_delivery_preserves_git_gates(self):
        for mode in ('Task','Group'):
            with self.subTest(mode=mode):
                project,root,g=self.setup_repo()
                self.enable(project,git_merge=mode,git_commit='Task',git_push='Group',usage_guard=False)
                task=self.task(project,'Human-reviewed outcome','review.py')
                claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
                (root/'review.py').write_text('verified artwork')
                run('task_update',dict(project=project,id=task,actor=self.agent,token=claim['token'],status='In Review',evidence='Camera and assertions verified'))
                delivered=run('task_update',dict(project=project,id=task,actor=self.agent,status='Done',evidence='Camera and assertions verified'))
                self.assertEqual(delivered['status'],'In Review')
                # Native publication or a later reconciliation is not approval.
                current=run('task_get',dict(project=project,id=task))
                self.assertEqual(current['status'],'In Review')
                self.assertTrue(current['review']['awaiting_human'])
                approved=run('task_review',dict(project=project,id=task,actor=self.human,decision='approve'))
                self.assertEqual(approved['status'],'Done',approved)
                self.assertEqual(g('show','main:review.py'),'verified artwork')
                self.assertEqual(g('rev-parse','main'),g('ls-remote','origin','refs/heads/main').split()[0])

    def test_open_group_keeps_its_branch_until_last_delivery(self):
        project,root,g=self.setup_repo()
        first=self.task(project,'First','a.py','Shared')
        second=self.task(project,'Second','b.py','Shared')
        claim=run('task_claim',dict(project=project,id=first,actor=self.agent));branch=claim['git']['branch']
        (root/'a.py').write_text('first')
        report=self.finish(project,first,claim)
        self.assertNotIn('checkout',report)
        self.assertEqual(git.state(root),(branch,[]))
        claim=run('task_claim',dict(project=project,id=second,actor=self.agent))
        (root/'b.py').write_text('second')
        report=self.finish(project,second,claim)
        self.assertTrue(report['checkout']['closed'])
        self.assertEqual(git.state(root),('main',[]))
        self.assertEqual(g('show','main:a.py'),'first')
        self.assertEqual(g('show','main:b.py'),'second')

    def test_new_claim_skips_retained_but_integrated_origin_chain(self):
        project,root,g=self.setup_repo()
        first=self.task(project,'First','a.py')
        claim=run('task_claim',dict(project=project,id=first,actor=self.agent));branch=claim['git']['branch']
        (root/'a.py').write_text('first')
        with patch.object(git,'close_verified_checkout',return_value={'closed':False,'reason':'Retained clean checkout'}):
            self.finish(project,first,claim)
        self.assertEqual(git.state(root),(branch,[]))
        g('branch','middle',branch)
        g('config','branch.'+branch+'.scrum-parent','middle')
        g('config','branch.middle.scrum-parent','main')
        second=self.task(project,'Second','b.py')
        next_claim=run('task_claim',dict(project=project,id=second,actor=self.agent))
        self.assertEqual(git.parent_branch(root,next_claim['git']['branch']),'main')
        self.assertEqual(g('show','HEAD:a.py'),'first')

    def test_unmerged_history_and_foreign_staging_are_preserved(self):
        project,root,g=self.setup_repo()
        task=self.task(project,'First','a.py')
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));branch=claim['git']['branch']
        (root/'a.py').write_text('verified')
        g('add','a.py');g('commit','-qm','Unmerged fixture history')
        self.assertEqual(git.integrated_origin(root,branch),branch)
        (root/'a.py').write_text('final verified')
        (root/'b.py').write_text('foreign staged work');g('add','b.py')
        before=g('diff','--cached','--binary')
        report=self.finish(project,task,claim)
        self.assertTrue(report['merge']['merged'])
        self.assertFalse(report['checkout']['closed'])
        self.assertNotIn('recovery_task',report)
        self.assertEqual(g('branch','--show-current'),branch)
        self.assertEqual(g('diff','--cached','--binary'),before)
        self.assertEqual((root/'b.py').read_text(),'foreign staged work')

    def test_failed_parent_publication_does_not_switch_checkout(self):
        project,root,g=self.setup_repo()
        task=self.task(project,'First','a.py')
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent));branch=claim['git']['branch']
        (root/'a.py').write_text('verified')
        failure=subprocess.CompletedProcess([],1,'','Simulated remote rejection')
        with patch.object(git,'publish_verified',return_value=(failure,1)),patch.object(git,'close_verified_checkout') as close:
            report=self.finish(project,task,claim)
        self.assertIn('error',report['merge'])
        self.assertIn('recovery_task',report)
        close.assert_not_called()
        self.assertEqual(git.state(root),(branch,[]))
