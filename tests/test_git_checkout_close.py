import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import test_storage
from store import run
import git_sync as git

FONT='''%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: Fixture
  m_AtlasPopulationMode: 1
  m_GetFontFeatures: 1
  m_FontFeatureTable:
    m_LigatureSubstitutionRecords: []
    m_GlyphPairAdjustmentRecords: []
  m_FallbackFontAssetTable: []
'''
FONT_PATH='Assets/Fonts/Font.asset'

class CheckoutCloseTest(unittest.TestCase):
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    def setUp(self):
        test_storage.StorageTest.setUp(self)
        self.p,self.workspace=self.project()
        self.g('init','-b','main');self.g('config','user.name','Fixture');self.g('config','user.email','fixture@example.test')
        (self.workspace/'.gitignore').write_text('.scrum/\nLogs/\n');(self.workspace/'product.txt').write_text('base\n')
        font=self.workspace/FONT_PATH;font.parent.mkdir(parents=True);font.write_text(FONT)
        self.g('add','.gitignore','product.txt',FONT_PATH);self.g('commit','-m','Initial')
        self.remote=self.root/'remote.git'
        subprocess.run(['git','init','--bare',str(self.remote)],check=True,capture_output=True)
        self.g('remote','add','origin',str(self.remote));self.g('push','-u','origin','main')
        run('project_settings',dict(project=self.p,actor=self.human,git_mode='Branches',git_merge='Task',git_push='Task',git_pause_blocked=True,autonomy_mode='Autonomous',max_parallel_agents=1,file_lock_policy='Auto'))
    def g(self,*args):
        r=subprocess.run(['git','-C',str(self.workspace),*args],capture_output=True,text=True,encoding='utf-8');self.assertEqual(r.returncode,0,r.stderr);return r.stdout.strip()
    def start(self):
        task=run('task_create',dict(project=self.p,title='Deliver product result',description='Concrete verified product outcome',acceptance_criteria=['Targeted behavior passed'],files=['product.txt']))
        claim=run('task_claim',dict(project=self.p,id=task['id'],actor=self.agent));self.assertTrue(claim['claimed'])
        (self.workspace/'product.txt').write_text('verified result\n')
        return task,claim
    def finish(self,task,claim):return run('work_finish',dict(project=self.p,id=task['id'],actor=self.agent,token=claim['token'],client='codex',evidence='Targeted behavior passed.'))
    def test_delivery_returns_to_recorded_parent_after_source_and_parent_publication(self):
        task,claim=self.start();r=self.finish(task,claim)
        self.assertEqual(r['completed']['status'],'Done');self.assertTrue(r['completed']['checkout']['closed'])
        self.assertEqual(git.state(self.workspace),('main',[]));self.assertEqual((self.workspace/'product.txt').read_text(),'verified result\n')
        self.assertEqual(self.g('rev-parse','HEAD'),self.g('ls-remote','origin','refs/heads/main').split()[0])
        self.assertTrue(git.is_ancestor(self.workspace,claim['git']['branch'],'main'))
    def test_generated_cache_and_output_are_retained_natively_without_recovery_task(self):
        task,claim=self.start();font=self.workspace/FONT_PATH
        generated=FONT.replace('    m_GlyphPairAdjustmentRecords: []','    m_GlyphPairAdjustmentRecords:\n    - cachedGlyph: 42')
        font.write_text(generated);output=self.workspace/'Artifacts/run/report.json';output.parent.mkdir(parents=True);output.write_text('{"test":"result"}')
        r=self.finish(task,claim);self.assertEqual(r['completed']['status'],'Done')
        kept=r['completed']['checkout']['retained'];archive=self.workspace/kept['archive']
        self.assertEqual((archive/'files'/FONT_PATH).read_text(),generated);self.assertEqual((archive/'moved/Artifacts/run/report.json').read_text(),'{"test":"result"}')
        self.assertEqual((self.workspace/FONT_PATH).read_text(),FONT);self.assertEqual(git.state(self.workspace),('main',[]))
        self.assertEqual(len(run('task_search',dict(project=self.p))['items']),1)
        self.assertNotIn(kept['private_branch'],self.g('ls-remote','--heads','origin'))
    def test_unplanned_authored_source_is_not_hidden_or_published(self):
        task,claim=self.start();path=self.workspace/'Assets/Missing.cs';path.write_text('authored implementation')
        r=self.finish(task,claim)
        self.assertEqual(r['completed']['status'],'In Review');self.assertTrue(r['completed']['integration_pending'])
        self.assertEqual(path.read_text(),'authored implementation');self.assertEqual(self.g('ls-tree','main','--','Assets/Missing.cs'),'')
        self.assertEqual(self.g('branch','--show-current'),claim['git']['branch'])
    def test_authored_font_change_is_not_mistaken_for_feature_cache(self):
        task,claim=self.start();font=self.workspace/FONT_PATH;font.write_text(FONT.replace('m_Name: Fixture','m_Name: New authored name'))
        r=self.finish(task,claim);self.assertEqual(r['completed']['status'],'In Review')
        self.assertIn('New authored name',font.read_text());self.assertEqual(self.g('branch','--show-current'),claim['git']['branch'])
    def test_changed_verified_file_after_commit_stays_unfinished(self):
        task,claim=self.start();original=git.close_verified_checkout
        def race(*args,**kwargs):
            (self.workspace/'product.txt').write_text('unverified concurrent edit\n')
            return original(*args,**kwargs)
        with patch('git_completion.git.close_verified_checkout',side_effect=race):r=self.finish(task,claim)
        self.assertEqual(r['completed']['status'],'In Review');self.assertEqual((self.workspace/'product.txt').read_text(),'unverified concurrent edit\n')
    def test_retention_requires_explicit_autonomous_preservation_policy(self):
        task,claim=self.start();font=self.workspace/FONT_PATH;font.write_text(FONT.replace('    m_LigatureSubstitutionRecords: []','    m_LigatureSubstitutionRecords:\n    - cached: 4'))
        run('project_settings',dict(project=self.p,actor=self.human,git_pause_blocked=False))
        r=self.finish(task,claim);self.assertEqual(r['completed']['status'],'In Review');self.assertTrue(font.exists())
    def test_clean_delivery_works_without_private_preservation_opt_in(self):
        run('project_settings',dict(project=self.p,actor=self.human,autonomy_mode='Standard',git_pause_blocked=False))
        task,claim=self.start();r=self.finish(task,claim);self.assertEqual(r['completed']['status'],'Done');self.assertEqual(git.state(self.workspace),('main',[]))
    def test_parent_is_recorded_origin_not_hardcoded_main(self):
        self.g('branch','integration');self.g('push','origin','integration')
        run('project_settings',dict(project=self.p,actor=self.human,git_base_branch='integration'))
        task,claim=self.start();r=self.finish(task,claim)
        self.assertEqual(r['completed']['status'],'Done');self.assertEqual(git.state(self.workspace),('integration',[]))
    def test_repeated_completion_does_not_repeat_retention_or_commits(self):
        task,claim=self.start();self.finish(task,claim);before=self.g('rev-list','--all','--count')
        r=self.finish(task,claim);self.assertTrue(r['completed']['already_done']);self.assertEqual(self.g('rev-list','--all','--count'),before)
        self.assertEqual(git.state(self.workspace),('main',[]))
    def test_worktree_policy_keeps_its_isolated_workspace(self):
        task,claim=self.start();source=claim['git']['branch'];sha=self.g('rev-parse','HEAD')
        r=git.close_verified_checkout(self.workspace,{'git_mode':'Worktrees'},source,'main',sha,task)
        self.assertTrue(r['closed']);self.assertEqual(self.g('branch','--show-current'),source)
    def test_another_live_recovery_claim_prevents_checkout_switch(self):
        task,claim=self.start();original=git._finish
        def concurrent(item):
            report=original(item)
            other=run('task_create',dict(project=self.p,title='Human recovery',description='Explicit concurrent recovery',acceptance_criteria=['Preserve work'],category='Git recovery',files=['other.txt']))
            held=run('task_claim',dict(project=self.p,id=other['id'],actor=self.human));self.assertTrue(held['claimed'])
            return report
        with patch('git_sync._finish',side_effect=concurrent):r=self.finish(task,claim)
        self.assertEqual(r['completed']['status'],'In Review');self.assertEqual(self.g('branch','--show-current'),claim['git']['branch'])

    def test_pending_checkout_does_not_claim_successor_and_reconciles_without_reimplementation(self):
        task,claim=self.start();other=run('task_create',dict(project=self.p,title='Next verified outcome',description='Separate task',acceptance_criteria=['Next result'],files=['next.txt']))
        extra=self.workspace/'Assets/Missing.cs';extra.write_text('unplanned authored source')
        result=run('work_finish',dict(project=self.p,id=task['id'],actor=self.agent,token=claim['token'],client='codex',evidence='Targeted behavior passed.',claim_next=True))
        self.assertTrue(result['next']['integration_pending']);self.assertFalse(result['next']['ownership_granted'])
        self.assertEqual(run('task_get',dict(project=self.p,id=other['id']))['status'],'TODO')
        before=self.g('rev-parse','main');extra.rename(self.root/'preserved-author-source.cs')
        reconciled=run('git_reconcile',dict(project=self.p,actor=self.agent,capability=git.FINALIZE_TOKEN,source=claim['git']['branch'],only_published=True))
        self.assertIn(task['id'],reconciled['completed']);self.assertEqual(self.g('rev-parse','main'),before)
        self.assertEqual(git.state(self.workspace),('main',[]));self.assertEqual((self.root/'preserved-author-source.cs').read_text(),'unplanned authored source')
