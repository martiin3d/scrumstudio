"""Coordination without extra model turns: supervisor pre-claim, claim_next, Claude hooks."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
import test_app
import test_storage
from store import run
import storage

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
SPEC=dict(description='Outcome',acceptance_criteria=['Checked.'])

class ClaimNextTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def test_finish_claims_and_loads_next_task_in_same_call(self):
        first=run('task_create',dict(project=self.p,title='First',**SPEC))['id']
        second=run('task_create',dict(project=self.p,title='Second',**SPEC))['id']
        token=run('task_claim',dict(project=self.p,id=first,actor=self.actor))['token']
        result=run('work_finish',dict(project=self.p,actor=self.actor,id=first,token=token,evidence='Checked.',claim_next=True))
        self.assertEqual(result['next']['task']['id'],second)
        self.assertEqual(result['next']['task']['acceptance_criteria'],['Checked.'])
        self.assertTrue(result['next']['claim']['claimed'])
        self.assertEqual(run('task_get',dict(project=self.p,id=second))['status'],'In Progress')
        with self.assertRaises(ValueError):
            run('work_finish',dict(project=self.p,actor=self.actor,id=second,token='x',evidence='Checked.',claim_next='yes'))

    def test_default_finish_still_only_selects(self):
        first=run('task_create',dict(project=self.p,title='First',**SPEC))['id']
        second=run('task_create',dict(project=self.p,title='Second',**SPEC))['id']
        token=run('task_claim',dict(project=self.p,id=first,actor=self.actor))['token']
        run('work_finish',dict(project=self.p,actor=self.actor,id=first,token=token,evidence='Checked.'))
        self.assertEqual(run('task_get',dict(project=self.p,id=second))['status'],'TODO')


class PreclaimTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def test_supervisor_claims_before_inference_and_falls_back(self):
        import agent_bridge
        task=run('task_create',dict(project=self.p,title='Offered',**SPEC))['id']
        rpc=Mock();rpc.call.side_effect=lambda name,args:run(name,args)
        observed={'session_used_percent':1,'weekly_used_percent':1,'observed_at':time.time()}
        started=agent_bridge.preclaim(rpc,self.p,self.actor,'codex',task,observed)
        self.assertEqual(started['task']['id'],task)
        text=agent_bridge.preclaimed_prompt(started)
        self.assertIn('Do NOT call work_start',text);self.assertIn(started['token'],text)
        self.assertIn('Checked.',text)
        failing=Mock();failing.call.side_effect=RuntimeError('down')
        self.assertEqual(agent_bridge.preclaim(failing,self.p,self.actor,'claude',task,observed),{})
        stopped=agent_bridge.preclaim(rpc,self.p,self.actor,'claude',task,{'session_used_percent':99,'weekly_used_percent':1,'observed_at':time.time()})
        self.assertTrue(stopped['should_stop'])


class ClaudeHooksTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def hook(self,action,cwd,env=None):
        import claude_hooks
        out=io.StringIO()
        with patch.dict(os.environ,env or {}),patch.object(sys,'stdin',io.StringIO(json.dumps({'cwd':str(cwd)}))),redirect_stdout(out):
            self.assertEqual(claude_hooks.main([action]),0)
        return out.getvalue()

    def test_session_start_context_and_silent_cases(self):
        project,folder=self.project()
        run('task_create',dict(project=project,title='Hooked task',**SPEC))
        storage.atomic_json(folder/'.scrum/local/claude-usage.json',{'session_used_percent':5,'weekly_used_percent':5,'observed_at':time.time()})
        (folder/'sub').mkdir()
        text=self.hook('session-start',folder/'sub')
        self.assertIn('Next eligible',text);self.assertIn('Hooked task',text);self.assertIn('Quota/Stop: ok',text)
        self.assertEqual(self.hook('prompt',folder),'')
        self.assertEqual(self.hook('session-start',folder,{'SCRUM_BRIDGE_WORKER':'1'}),'')
        with tempfile.TemporaryDirectory() as other:
            self.assertEqual(self.hook('session-start',other),'')
        self.assertEqual(self.hook('session-end',folder),'')

    def test_setup_merges_hooks_without_touching_other_settings(self):
        import setup
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'settings.json'
            path.write_text(json.dumps({'statusLine':{'type':'command','command':'mine'},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'other'}]}]}}))
            for _ in range(2):setup.install_claude_hooks(path,Path(temp))
            data=json.loads(path.read_text())
            self.assertEqual(data['statusLine']['command'],'mine')
            self.assertEqual(len(data['hooks']['Stop']),2)
            self.assertEqual(len(data['hooks']['SessionStart']),1)
            self.assertIn('claude_hooks.py',data['hooks']['SessionStart'][0]['hooks'][0]['command'])

class EditSummaryTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def repo(self,folder):
        import subprocess
        git=lambda *a:subprocess.run(['git','-C',str(folder),*a],check=True,capture_output=True)
        git('init','-q');(folder/'a.py').write_text('a\n');git('add','a.py')
        git('-c','user.email=t@t','-c','user.name=t','commit','-qm','init')
        (folder/'a.py').write_text('a\nb\n');(folder/'new.py').write_text('n\n')

    def test_pause_handoff_records_edits_without_model_progress(self):
        project,folder=self.project()
        self.repo(folder)
        task=run('task_create',dict(project=project,title='Edit',files=['a.py'],**SPEC))['id']
        run('task_claim',dict(project=project,id=task,actor=self.agent))
        run('agent_checkpoint',dict(project=project,actor=self.agent,client='codex',action='pause',summary=''))
        continuity=run('task_get',dict(project=project,id=task))['continuity']
        self.assertIn('Changed planned files: a.py (+1/-0)',continuity)
        self.assertNotIn('No progress recorded',continuity)

    def test_crashed_claim_reports_edits_on_resume(self):
        project,folder=self.project()
        self.repo(folder)
        task=run('task_create',dict(project=project,title='Crash',files=['a.py'],**SPEC))['id']
        run('task_claim',dict(project=project,id=task,actor=self.agent,lease_seconds=60))
        import sqlite3
        with sqlite3.connect(folder/'.scrum/local/scrum.db') as db:db.execute('UPDATE tasks SET lock_until=1 WHERE id=?',(task,))
        resumed=run('session_resume',dict(project=project,actor=self.agent))['resume'][0]
        self.assertTrue(resumed['interrupted'])
        self.assertIn('a.py',resumed['edits'])

    def test_no_repository_adds_nothing(self):
        import git_changes
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(git_changes.summary(folder,['a.py']),'')
            self.assertEqual(git_changes.review(folder,['a.py']),{'git':False})
            self.assertEqual(git_changes.dirty(folder,['a.py']),[])
        self.assertEqual(git_changes.summary('',[]),'')
        self.assertEqual(git_changes.review('',[]),{})

class ChangeReviewTest(unittest.TestCase):
    """task_changes: one bounded git-derived diff per task, no model call."""
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    repo=EditSummaryTest.repo

    def test_claim_reports_planned_files_already_edited(self):
        project,folder=self.project()
        self.repo(folder)
        task=run('task_create',dict(project=project,title='Edit',files=['a.py','other.py'],**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
        self.assertEqual(claim['dirty_planned'],['a.py'])
        # Renewal is not a fresh claim: no baseline repeated.
        renewed=run('task_claim',dict(project=project,id=task,actor=self.agent,token=claim['token']))
        self.assertNotIn('dirty_planned',renewed)
        clean=run('task_create',dict(project=project,title='Clean',files=['other.py'],**SPEC))['id']
        run('task_release',dict(project=project,id=task,actor=self.agent,token=claim['token']))
        self.assertNotIn('dirty_planned',run('task_claim',dict(project=project,id=clean,actor=self.agent)))

    def test_task_changes_returns_bounded_diff_and_unplanned_files(self):
        project,folder=self.project()
        self.repo(folder)
        (folder/'.scrum').mkdir(exist_ok=True);(folder/'.scrum'/'x.json').write_text('{}')
        task=run('task_create',dict(project=project,title='Edit',files=['a.py'],**SPEC))['id']
        view=run('task_changes',dict(project=project,id=task))
        self.assertTrue(view['git']);self.assertEqual(view['planned'],['a.py'])
        self.assertEqual([f['path'] for f in view['files']],['a.py','new.py'])  # .scrum metadata hidden
        self.assertEqual(view['files'][0],{'path':'a.py','planned':True,'added':1,'removed':0})
        self.assertEqual(view['unplanned'],['new.py'])
        self.assertIn('+b',view['diff']);self.assertNotIn('new.py',view['diff'])
        self.assertNotIn('truncated',view)
        subset=run('task_changes',dict(project=project,id=task,files=['new.py']))
        self.assertIn('+++ new.py (new file, 1 lines)',subset['diff']);self.assertIn('+n',subset['diff'])
        self.assertEqual(subset['planned'],['a.py'])
        self.assertNotIn('diff',run('task_changes',dict(project=project,id=task,diff=False)))
        self.assertEqual(run('task_changes',dict(project=project,id=task,files=['none.py']))['diff'],'')
        with self.assertRaises(ValueError):run('task_changes',dict(project=project,id=task+99))

    def test_task_changes_truncates_large_diffs(self):
        project,folder=self.project()
        self.repo(folder)
        (folder/'a.py').write_text(''.join(f'line {i}\n' for i in range(400)))
        task=run('task_create',dict(project=project,title='Big',files=['a.py'],**SPEC))['id']
        view=run('task_changes',dict(project=project,id=task,max_lines=30))
        self.assertTrue(view['truncated']);self.assertIn('files=[subset]',view['hint'])
        self.assertEqual(len(view['diff'].splitlines()),30);self.assertGreater(view['diff_lines'],400)
        clean=run('task_create',dict(project=project,title='Clean',**SPEC))['id']
        (folder/'a.py').write_text('a\n');(folder/'new.py').unlink()
        self.assertTrue(run('task_changes',dict(project=project,id=clean))['clean'])

    def test_worker_catalog_and_bridge_allowlist_expose_task_changes(self):
        import mcp_server
        sys.path.insert(0,str(SCRIPTS));import agent_bridge
        request=dict(jsonrpc='2.0',id=1,method='tools/list')
        with patch.object(sys,'argv',['mcp_server.py','--profile','worker']):worker=mcp_server.handle(request)['result']['tools']
        tool=next(t for t in worker if t['name']=='task_changes')
        self.assertEqual(set(tool['inputSchema']['properties']),{'project','id','files','max_lines','diff'})
        self.assertIn('task_changes',agent_bridge.SCRUM_WORK_TOOLS)

class GitAutomationTest(unittest.TestCase):
    """Server-side branch/commit/push per context group; git never blocks coordination."""
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def repo(self,folder):
        import subprocess
        git=lambda *a:subprocess.run(['git','-C',str(folder),*a],check=True,capture_output=True)
        git('init','-q','-b','main');git('config','user.email','t@t');git('config','user.name','t')
        (folder/'.gitignore').write_text('.scrum/\n');(folder/'a.py').write_text('a\n');(folder/'b.py').write_text('b\n')
        git('add','.');git('commit','-qm','init')
        remote=folder.parent/(folder.name+'-remote.git');subprocess.run(['git','init','-q','--bare',str(remote)],check=True)
        git('remote','add','origin',str(remote));git('push','-q','-u','origin','main')
        return lambda *a:subprocess.run(['git','-C',str(folder),*a],capture_output=True,text=True).stdout.strip()

    def enable(self,project,**more):
        return run('project_settings',dict(project=project,actor=self.human,git_mode='Branches',git_base_branch='main',max_parallel_agents=5,**more))

    def test_settings_validation_and_defaults(self):
        project,folder=self.project()
        s=run('project_settings',dict(project=project))
        self.assertEqual((s['git_mode'],s['git_commit'],s['git_push'],s['git_branch_prefix'],s['git_remote']),('Off','Task','Group','scrum/','origin'))
        for bad in [dict(git_mode='Auto'),dict(git_push='Always'),dict(git_remote=''),dict(git_base_branch='two words'),dict(git_branch_prefix='-x')]:
            with self.assertRaises(ValueError):run('project_settings',dict(project=project,actor=self.human,**bad))
        with self.assertRaises(ValueError):run('project_settings',dict(project=project,actor=self.agent,git_mode='Branches'))
        import git_sync
        self.assertEqual(git_sync.branch_for({'git_branch_prefix':'scrum/'},{'code':'WEB-7','title':'Fix Login Página','context_group':''}),'scrum/web-7-fix-login-pagina')
        self.assertEqual(git_sync.branch_for({'git_branch_prefix':'f/'},{'code':'WEB-7','title':'x','context_group':'Auth flow'}),'f/auth-flow')

    def test_off_mode_never_touches_git(self):
        project,folder=self.project()
        git=self.repo(folder)
        task=run('task_create',dict(project=project,title='Edit',files=['a.py'],**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
        self.assertNotIn('git',claim)
        (folder/'a.py').write_text('a\nx\n')
        done=run('task_update',dict(project=project,id=task,actor=self.agent,token=claim['token'],status='Done',evidence='ok'))
        self.assertNotIn('git',done);self.assertEqual(git('branch','--show-current'),'main');self.assertEqual(git('status','--porcelain'),'M a.py')

    def test_group_branch_commit_per_task_and_push_when_group_completes(self):
        project,folder=self.project()
        git=self.repo(folder)
        self.enable(project)
        first=run('task_create',dict(project=project,title='Fix login',files=['a.py'],context_group='Auth flow',**SPEC))['id']
        second=run('task_create',dict(project=project,title='Fix logout',files=['b.py'],context_group='Auth flow',**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=first,actor=self.agent))
        self.assertEqual(claim['git'],{'branch':'scrum/auth-flow','created':True,'previous':'main'})
        self.assertEqual(git('branch','--show-current'),'scrum/auth-flow')
        (folder/'a.py').write_text('a\nx\n')
        finish=run('work_finish',dict(project=project,actor=self.agent,id=first,token=claim['token'],evidence='ok'))
        report=finish['completed']['git']
        self.assertTrue(report['commit']['committed']);self.assertEqual(report['push']['reason'],'waiting for the rest of the context group')
        self.assertEqual(git('log','-1','--format=%s'),'PRO-1: Fix login');self.assertEqual(git('status','--porcelain'),'')
        self.assertEqual(git('ls-remote','--heads','origin','scrum/auth-flow'),'')
        other=run('user_register',{'identity':'agent:second','name':'Second','kind':'agent'})['id']
        run('agent_checkpoint',{'project':project,'actor':other,'client':'codex','session_used_percent':10,'weekly_used_percent':20})
        claim2=run('task_claim',dict(project=project,id=second,actor=other))
        self.assertEqual(claim2['git'],{'branch':'scrum/auth-flow'})  # same group, same branch, nothing to switch
        (folder/'b.py').write_text('b\nx\n')
        done=run('task_update',dict(project=project,id=second,actor=other,token=claim2['token'],status='Done',evidence='ok'))
        self.assertEqual(done['git']['push'],{'queued':True});self.assertEqual(done['git_push'][0]['pushed'],True)
        self.assertNotEqual(git('ls-remote','--heads','origin','scrum/auth-flow'),'')
        texts=[e['text'] for e in run('task_activity',dict(project=project,id=second))['items']]
        self.assertTrue(any(t.startswith('git: pushed scrum/auth-flow to origin') for t in texts))

    def test_dirty_checkout_of_other_branch_refuses_claim_and_reports(self):
        project,folder=self.project()
        git=self.repo(folder)
        self.enable(project)
        grouped=run('task_create',dict(project=project,title='Grouped',files=['a.py'],context_group='Auth flow',**SPEC))['id']
        single=run('task_create',dict(project=project,title='Readme tweak',files=['b.py'],**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=grouped,actor=self.agent))
        (folder/'a.py').write_text('a\nx\n')
        other=run('user_register',{'identity':'agent:second','name':'Second','kind':'agent'})['id']
        run('agent_checkpoint',{'project':project,'actor':other,'client':'codex','session_used_percent':10,'weekly_used_percent':20})
        refused=run('task_claim',dict(project=project,id=single,actor=other))
        self.assertFalse(refused['claimed']);self.assertEqual(refused['branch_busy']['dirty'],['a.py'])
        self.assertEqual(refused['branch_busy']['wanted'],'scrum/pro-2-readme-tweak')
        self.assertEqual(run('task_get',dict(project=project,id=single))['status'],'TODO')
        self.assertTrue(any(e['text'].startswith('git: claim refused') for e in run('task_activity',dict(project=project,id=single))['items']))
        # Same-branch work is still claimable and a renewal never switches branches.
        renewed=run('task_claim',dict(project=project,id=grouped,actor=self.agent,token=claim['token']))
        self.assertNotIn('git',renewed)
        # Once the group work is committed the single task gets its own branch from main and pushes at Done.
        run('work_finish',dict(project=project,actor=self.agent,id=grouped,token=claim['token'],evidence='ok'))
        claim2=run('task_claim',dict(project=project,id=single,actor=other))
        self.assertEqual(claim2['git']['branch'],'scrum/pro-2-readme-tweak');self.assertTrue(claim2['git']['created'])
        self.assertEqual(git('log','-1','--format=%s'),'init')  # branched from main, not from the group branch
        (folder/'b.py').write_text('b\nx\n')
        done=run('work_finish',dict(project=project,actor=other,id=single,token=claim2['token'],evidence='ok'))
        self.assertTrue(done['completed']['git_push'][0]['pushed'])

    def test_push_failure_is_reported_and_done_stands(self):
        project,folder=self.project()
        git=self.repo(folder)
        self.enable(project,git_remote='nowhere',git_push='Task')
        task=run('task_create',dict(project=project,title='Edit',files=['a.py'],**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=task,actor=self.agent))
        (folder/'a.py').write_text('a\nx\n')
        done=run('task_update',dict(project=project,id=task,actor=self.agent,token=claim['token'],status='Done',evidence='ok'))
        self.assertEqual(done['status'],'Done');self.assertTrue(done['git']['commit']['committed'])
        self.assertFalse(done['git_push'][0]['pushed']);self.assertIn('nowhere',done['git_push'][0]['error'])
        self.assertTrue(any(e['text'].startswith('git: PUSH FAILED') for e in run('task_activity',dict(project=project,id=task))['items']))
        # Nothing to commit is reported, not an error.
        again=run('task_create',dict(project=project,title='Noop',files=['b.py'],**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=again,actor=self.agent))
        done=run('task_update',dict(project=project,id=again,actor=self.agent,token=claim['token'],status='Done',evidence='ok'))
        self.assertEqual(done['git']['commit'],{'committed':False,'reason':'nothing to commit'})

    def test_group_commit_mode_waits_for_last_task(self):
        project,folder=self.project()
        git=self.repo(folder)
        self.enable(project,git_commit='Group',git_push='Never')
        first=run('task_create',dict(project=project,title='One',files=['a.py'],context_group='G',**SPEC))['id']
        second=run('task_create',dict(project=project,title='Two',files=['b.py'],context_group='G',**SPEC))['id']
        claim=run('task_claim',dict(project=project,id=first,actor=self.agent))
        (folder/'a.py').write_text('a\nx\n')
        done=run('task_update',dict(project=project,id=first,actor=self.agent,token=claim['token'],status='Done',evidence='ok'))
        self.assertFalse(done['git']['commit']['committed']);self.assertEqual(git('status','--porcelain'),'M a.py')
        claim=run('task_claim',dict(project=project,id=second,actor=self.agent))
        (folder/'b.py').write_text('b\nx\n')
        done=run('task_update',dict(project=project,id=second,actor=self.agent,token=claim['token'],status='Done',evidence='ok'))
        self.assertTrue(done['git']['commit']['committed']);self.assertEqual(git('log','-1','--format=%s'),'G: PRO-1, PRO-2')
        self.assertEqual(done['git']['push']['reason'],'push disabled');self.assertNotIn('git_push',done)

if __name__=='__main__':unittest.main()
