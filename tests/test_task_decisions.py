import json
import unittest
from unittest.mock import patch
from pathlib import Path
import test_storage
from store import run
import task_decisions
import scrum_cli

class DecisionsTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone
    def setUp(self):
        test_storage.StorageTest.setUp(self);self.p,self.rootpath=self.project()
    def task(self,title='Task',files=None):return run('task_create',dict(project=self.p,title=title,description='Authorized concrete change',acceptance_criteria=['Result verified'],files=files or ['Assets/UI/Home.prefab']))
    def entry(self,key='background-fit',**extra):return dict(key=key,decision='Preserve aspect ratio and fill the parent.',reason='Intentional human layout change replaces the old fixed sizing.',files=['Assets/UI/**'],**extra)
    def delivered(self,key='background-fit',**extra):
        t=self.task(key);run('task_update',dict(project=self.p,id=t['id'],actor=self.human,decisions=[self.entry(key,**extra)],status='Done',evidence='Verified layout'))
        return t,str(t['number'])+':'+key if 'number' in t else t['code'].split('-')[-1]+':'+key
    def get(self,t,**extra):return run('task_get',dict(project=self.p,id=t['id'],**extra))
    def test_only_related_completed_decisions_are_loaded_and_versions_invalidate(self):
        target=self.task('Future audit');before=self.get(target);self.assertNotIn('decision_context',before)
        source,ref=self.delivered();current=self.get(target);self.assertEqual(current['decision_context']['items'][0]['ref'],ref);self.assertNotEqual(before['version'],current['version'])
        self.assertFalse(self.get(target,version_seen=current['version'])['changed'])
        unrelated=self.task('Backend audit',['Runtime/Save.cs']);self.assertNotIn('decision_context',self.get(unrelated))
        self.assertEqual(self.get(unrelated,decision_files=['Assets/UI/**'])['decision_context']['items'][0]['source_code'],source['code'])
    def test_proposed_review_and_cancelled_records_are_not_current(self):
        source=self.task('Proposed');target=self.task('Audit')
        run('task_update',dict(project=self.p,id=source['id'],actor=self.human,decisions=[self.entry()],status='In Review'))
        self.assertNotIn('decision_context',self.get(target));self.assertEqual(len(self.get(source)['decisions']),1)
        run('task_update',dict(project=self.p,id=source['id'],actor=self.human,status='Cancelled',cancel_reason='No longer intended'))
        self.assertNotIn('decision_context',self.get(target))
    def test_explicit_replacement_preserves_source_and_changes_only_current_context(self):
        old,ref=self.delivered();new=self.task('Replacement');target=self.task('Audit')
        with self.assertRaisesRegex(ValueError,'Explicitly supersede'):run('task_update',dict(project=self.p,id=new['id'],actor=self.human,decisions=[self.entry()]))
        self.assertNotIn('decisions',self.get(new))
        replacement=self.entry(supersedes=[ref]);replacement['decision']='Keep centered containment instead of fill.'
        run('task_update',dict(project=self.p,id=new['id'],actor=self.human,decisions=[replacement],status='Done',evidence='New human requirement verified'))
        self.assertEqual(self.get(old)['decisions'][0]['decision'],self.entry()['decision'])
        self.assertEqual([x['source_code'] for x in self.get(target)['decision_context']['items']],[new['code']])
        with self.assertRaisesRegex(ValueError,'immutable'):run('task_update',dict(project=self.p,id=old['id'],actor=self.human,decisions=[]))
    def test_agent_finish_records_decisions_in_existing_call_and_requires_own_claim(self):
        source=self.task('Implement intentional layout');target=self.task('Later audit')
        with self.assertRaisesRegex(ValueError,'live task claim'):run('task_update',dict(project=self.p,id=source['id'],actor=self.agent,decisions=[self.entry()]))
        cl=run('task_claim',dict(project=self.p,id=source['id'],actor=self.agent))
        result=run('work_finish',dict(project=self.p,id=source['id'],actor=self.agent,token=cl['token'],evidence='Layout verified',decisions=[self.entry()]))
        self.assertEqual(result['completed']['status'],'Done');self.assertEqual(len(self.get(target)['decision_context']['items']),1)
        with self.assertRaisesRegex(ValueError,'live task claim'):run('task_update',dict(project=self.p,id=source['id'],actor=self.agent,decisions=[]))
    def test_invalid_record_rolls_back_completion(self):
        source=self.task('Invalid record');cl=run('task_claim',dict(project=self.p,id=source['id'],actor=self.agent))
        bad=self.entry(supersedes=['999:background-fit'])
        with self.assertRaises(ValueError):run('work_finish',dict(project=self.p,id=source['id'],actor=self.agent,token=cl['token'],evidence='Verified',decisions=[bad]))
        self.assertEqual(self.get(source)['status'],'In Progress');self.assertNotIn('decisions',self.get(source))
    def test_pagination_and_files_page_do_not_lose_matching_scope(self):
        for i in range(7):self.delivered('intent-'+str(i))
        target=self.task('Wide audit',['Runtime/'+str(i)+'.cs' for i in range(22)]+['Assets/UI/Home.prefab'])
        first=self.get(target);ctx=first['decision_context'];self.assertEqual(len(ctx['items']),5);self.assertTrue(ctx['has_more'])
        rest=self.get(target,decisions_after=ctx['next_after'])['decision_context'];self.assertEqual(len(rest['items']),2)
        self.assertNotIn('Assets/UI/Home.prefab',first['files']);self.assertEqual(rest['total'],7)
    def test_snapshot_roundtrip_preserves_records_when_codes_change(self):
        source,ref=self.delivered();run('project_export',{'project':self.p});opened,clone=self.clone(self.rootpath)
        restored=run('task_search',dict(project=opened['id'],query='background-fit'))['items'][0]
        t=run('task_get',dict(project=opened['id'],id=restored['id']));self.assertEqual(t['decisions'],task_decisions.validate([self.entry()]))
        target=run('task_create',dict(project=opened['id'],title='Imported audit',description='Review delivered intent',acceptance_criteria=['Respected'],files=['Assets/UI/Home.prefab']))
        self.assertEqual(run('task_get',dict(project=opened['id'],id=target['id']))['decision_context']['items'][0]['ref'],ref)
    def test_bounded_validation_rejects_unsafe_or_global_scopes(self):
        for scope in (['../secret'],['/absolute'],['C:/local'],['**'],[],['Assets/*'],['Assets/./UI']):
            with self.subTest(scope=scope),self.assertRaises(ValueError):task_decisions.validate([{**self.entry(),'files':scope}])
        with self.assertRaises(ValueError):task_decisions.validate([self.entry()]*6)
        with self.assertRaises(ValueError):task_decisions.validate([{**self.entry(),'reason':''}])
    def test_cli_finish_flag_and_schema_carry_decisions(self):
        import mcp_server
        spec=next(s for n,d,s in mcp_server.TOOLS if n=='work_finish');self.assertIn('decisions',spec['properties'])
        ns=scrum_cli.parser().parse_args(['finish','--decisions-file','intent.json']);self.assertEqual(ns.decisions_file,Path('intent.json'))

    def test_cli_flag_records_decisions_through_the_native_finish(self):
        import io
        source=self.task('CLI layout change');target=self.task('CLI future audit')
        context=self.root/'cli-private.json';scrum_cli.atomic(context,dict(schema=1,project=self.p,actor=self.agent,client='codex',batch=False,current=None,receipts=[]))
        scrum_cli.coordinated('work_start',dict(id=source['id'],claim=True,session_used_percent=10,weekly_used_percent=20),context)
        file=self.root/'intent.json';file.write_text(json.dumps([self.entry()]),encoding='utf-8')
        with patch('sys.stdout',new=io.StringIO()) as out:
            code=scrum_cli.main(['finish','--context',str(context),'--task',str(source['id']),'--evidence','Layout verified','--decisions-file',str(file)])
        self.assertEqual(code,0,out.getvalue());self.assertEqual(self.get(source)['status'],'Done');self.assertEqual(len(self.get(target)['decision_context']['items']),1)

class GitDecisionDeliveryTest(unittest.TestCase):
    import test_git_checkout_close as fixture
    setUp=fixture.CheckoutCloseTest.setUp
    tearDown=fixture.CheckoutCloseTest.tearDown
    project=fixture.CheckoutCloseTest.project
    g=fixture.CheckoutCloseTest.g
    start=fixture.CheckoutCloseTest.start
    def record(self):return dict(key='product-intent',decision='Keep the verified product behavior.',reason='Intentional requirement.',files=['product.txt'])
    def target(self):return run('task_create',dict(project=self.p,title='Later product audit',description='Keep delivered intent',acceptance_criteria=['Respected'],files=['product.txt']))
    def test_decisions_activate_after_real_git_integration(self):
        source,claim=self.start();target=self.target()
        result=run('work_finish',dict(project=self.p,id=source['id'],actor=self.agent,token=claim['token'],client='codex',evidence='Verified targeted result',decisions=[self.record()]))
        self.assertEqual(result['completed']['status'],'Done')
        self.assertEqual(run('task_get',dict(project=self.p,id=target['id']))['decision_context']['items'][0]['source_code'],source['code'])
    def test_failed_integration_does_not_promote_a_proposed_decision(self):
        source,claim=self.start();target=self.target()
        with patch('git_sync.merge_completed',return_value={'error':'Simulated integration failure'}):
            result=run('work_finish',dict(project=self.p,id=source['id'],actor=self.agent,token=claim['token'],client='codex',evidence='Verified targeted result',decisions=[self.record()]))
        self.assertEqual(result['completed']['status'],'In Review')
        self.assertNotIn('decision_context',run('task_get',dict(project=self.p,id=target['id'])))
