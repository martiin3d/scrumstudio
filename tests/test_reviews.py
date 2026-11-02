import json
from pathlib import Path
import unittest
from unittest.mock import patch

import mcp_server
import scrum_cli as cli
import test_storage
from store import run


class ReviewTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def task(self,p):
        task=run('task_create',dict(project=p,title='Review the existing artwork',description='Deliver an image and request human review',acceptance_criteria=['Verified deliverable with review references']))['id']
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        run('task_attachment',dict(project=p,id=task,actor=self.agent,token=claim['token'],action='add',name='reference.txt',content_base64='cmVmZXJlbmNl'))
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],status='In Review',evidence='33 passing assertions; camera reviewed'))
        return task

    def review(self,p,task,decision='request_changes',**extra):
        return run('task_review',dict(project=p,id=task,actor=self.human,decision=decision,**extra))

    def test_changes_require_feedback_and_only_humans_can_decide(self):
        p,_=self.project();task=self.task(p)
        with self.assertRaisesRegex(ValueError,'Describe the requested changes'):self.review(p,task,comment='  ')
        with self.assertRaisesRegex(ValueError,'Only an active human'):
            run('task_review',dict(project=p,id=task,actor=self.agent,decision='approve'))
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'In Review')

    def test_feedback_is_durable_and_revised_work_waits_for_approval(self):
        p,folder=self.project();task=self.task(p)
        before=run('task_get',dict(project=p,id=task))
        decision=self.review(p,task,comment='Make the selected button orange.',expected_version=before['review']['version'])
        self.assertEqual(decision['status'],'TODO')
        current=run('task_get',dict(project=p,id=task))
        self.assertEqual(current['description'],before['description'])
        self.assertEqual(current['evidence'],'')
        self.assertEqual(current['attachment_count'],1)
        self.assertIn('Make the selected button orange.',current['continuity'])
        self.assertEqual(current['review']['author'],'human:portable')
        self.assertEqual(current['review']['state'],'changes_requested')
        path=folder/'private.json'
        cli.atomic(path,dict(schema=1,project=p,actor=self.agent,client='codex',current=None,batch=False,receipts=[]))
        cli.coordinated('work_start',dict(id=task,claim=True,session_used_percent=1,weekly_used_percent=1),path)
        revised=cli.coordinated('work_finish',dict(id=task,evidence='34 passing assertions; new capture attached'),path)
        self.assertEqual(revised['completed']['status'],'In Review')
        self.assertTrue(revised['completed']['awaiting_human_review'])
        self.assertIsNone(cli.context_file(path)['current'])
        self.assertEqual(run('file_locks',dict(project=p))['items'],[])
        # A direct Done write cannot substitute for the human decision.
        self.assertEqual(run('task_update',dict(project=p,id=task,actor=self.agent,status='Done',evidence='34 assertions'))['status'],'In Review')
        approved=self.review(p,task,'approve')
        self.assertEqual(approved['status'],'Done')
        self.assertEqual(run('task_get',dict(project=p,id=task))['review']['state'],'approved')

    def test_stale_attachment_view_cannot_be_approved(self):
        p,_=self.project();task=self.task(p)
        version=run('task_get',dict(project=p,id=task))['review']['version']
        run('task_attachment',dict(project=p,id=task,actor=self.human,action='add',name='new-capture.txt',content_base64='bmV3'))
        with self.assertRaisesRegex(ValueError,'Task changed'):self.review(p,task,'approve',expected_version=version)
        self.assertEqual(run('task_get',dict(project=p,id=task))['status'],'In Review')

    def test_approval_cannot_bypass_missing_verification(self):
        p,_=self.project();task=self.task(p)
        run('task_update',dict(project=p,id=task,actor=self.agent,evidence=''))
        with self.assertRaises(ValueError):self.review(p,task,'approve')
        current=run('task_get',dict(project=p,id=task))
        self.assertEqual(current['status'],'In Review')
        self.assertNotEqual(current['review'].get('state'),'approved')

    def test_stop_is_preserved_and_review_does_not_launch_disabled_agents(self):
        p,_=self.project();task=self.task(p)
        stopped=run('project_control',dict(project=p,actor=self.human,action='stop'))
        with patch('managed_agents.subprocess.Popen') as spawn:
            result=self.review(p,task,comment='Adjust the spacing.')
        spawn.assert_not_called()
        self.assertEqual(result['review_resume']['status'],'stopped')
        control=run('agent_status',dict(project=p))['control']
        self.assertTrue(control['paused'])
        self.assertEqual(control['generation'],stopped['control']['generation'])

    def test_feedback_signals_connected_worker_without_model_or_duplicate_launch(self):
        p,_=self.project();task=self.task(p)
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='observe',running=False,session_used_percent=1,weekly_used_percent=1))
        before=run('agent_status',dict(project=p))['control']['generation']
        with patch('managed_agents.subprocess.Popen') as spawn:result=self.review(p,task,comment='Use a larger title.')
        spawn.assert_not_called()
        self.assertEqual(result['review_resume']['status'],'signal_sent')
        self.assertGreater(run('agent_status',dict(project=p))['control']['generation'],before)

    def test_individually_disabled_profile_is_not_started_by_feedback(self):
        p,_=self.project();task=self.task(p)
        profile=run('agent_manage',dict(project=p,actor=self.human,action='create',name='Disabled reviewer worker',client='codex',start_immediately=False))
        run('task_update',dict(project=p,id=task,actor=self.human,owner_id=profile['actor']))
        with patch('managed_agents.subprocess.Popen') as spawn:
            self.review(p,task,comment='Change the padding.')
        spawn.assert_not_called()
        saved=run('agent_manage',dict(project=p,action='get',id=profile['id']))
        self.assertFalse(saved['enabled']);self.assertFalse(saved['resume_on_wakeup'])

    def test_review_roundtrip_is_preserved_by_project_export_restore(self):
        p,folder=self.project();task=self.task(p)
        self.review(p,task,comment='Keep the illustration; change the border.')
        run('project_export',dict(project=p))
        snapshot=json.loads((folder/'.scrum/snapshot.json').read_text())
        self.assertEqual(snapshot['tables']['tasks'][0]['review_data']['state'],'changes_requested')
        run('project_restore',dict(project=p,confirm=True))
        current=run('task_get',dict(project=p,id=task))
        self.assertEqual(current['review']['comment'],'Keep the illustration; change the border.')

    def test_cli_and_mcp_share_human_review_validation(self):
        p,_=self.project()
        for channel in ('cli','mcp'):
            task=self.task(p)
            args=dict(project=p,id=task,actor=self.human,decision='request_changes',comment='Change the accent color.')
            if channel=='cli':result=cli.invoke('task_review',args)
            else:
                response=mcp_server.handle(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='task_review',arguments=args)))['result']
                self.assertFalse(response.get('isError'),response)
                result=json.loads(response['content'][0]['text'])
            self.assertEqual(result['status'],'TODO')
