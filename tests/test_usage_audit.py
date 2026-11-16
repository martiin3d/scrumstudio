import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch,Mock
import tempfile
import test_storage
from store import run
import execution_usage
import usage_audit

class UsageAuditTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def test_old_in_process_flow_module_keeps_unknown_fields_null(self):
        p,root=self.project()
        with patch('telemetry.metrics',return_value={'completed_tasks':2}):
            result=run('coordination_metrics',dict(project=p,include_audit=True,start_at=0,end_at=1))['audit']
        self.assertIsNone(result['flow']['attempted_tasks'])
        self.assertEqual(result['flow']['completed_tasks'],2)
        self.assertEqual(result['live_executions'],[])

    def test_opt_in_isolated_and_shared_run_not_divided(self):
        p,root=self.project();task=run('task_create',dict(project=p,title='Recover',description='One verified recovery',acceptance_criteria=['Published'],category='Git recovery'))['id']
        report=dict(run_id='one',model='fixture',tasks=[task],source='codex-terminal-turn',input_tokens=100,output_tokens=10,cached_input_tokens=80,cache_write_input_tokens=0,reasoning_output_tokens=2)
        run('agent_checkpoint',dict(project=p,actor=self.agent,client='codex',action='offline',cli_usage_report=report))
        self.assertNotIn('audit',run('coordination_metrics',dict(project=p)))
        result=run('coordination_metrics',dict(project=p,include_audit=True))['audit']
        self.assertEqual(result['recorded']['runs'],1)
        self.assertEqual(result['recorded']['known_totals']['uncached_input_tokens'],20)
        self.assertEqual(result['top_runs'][0]['tasks'][0]['id'],task)
        self.assertTrue(result['recommendations'])
        other,_=self.project('Other');self.assertEqual(run('coordination_metrics',dict(project=other,include_audit=True))['audit']['recorded']['runs'],0)
        empty=run('coordination_metrics',dict(project=p,include_audit=True,start_at=0,end_at=1))['audit']
        self.assertEqual(empty['recorded']['runs'],0)

    def test_logs_are_bounded_deduplicated_and_never_export_text(self):
        p,root=self.project();path=root/'.scrum/local/worker-codex-fixture.log'
        events=[{'type':'thread.started','thread_id':'one'}, {'type':'item.completed','item':{'text':'SECRET not for audit'}}, {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':2,'cached_input_tokens':90}}, {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':2,'cached_input_tokens':90}}, {'type':'thread.started','thread_id':'unknown'}]
        path.write_text('\n'.join(json.dumps(x) for x in events))
        result=run('coordination_metrics',dict(project=p,include_audit=True,include_logs=True))['audit']
        h=result['historical_logs'];self.assertEqual(h['runs'],2);self.assertEqual(h['known_runs'],1)
        self.assertEqual(h['known_totals']['input_tokens'],100)
        self.assertNotIn('SECRET',json.dumps(result));self.assertTrue(h['partial'])

    def test_unknown_cache_and_verification_repetitions_are_honest(self):
        totals=usage_audit.totals([{'input_tokens':100,'output_tokens':2},{}])
        self.assertIsNone(totals['known_totals']['cached_input_tokens']);self.assertTrue(totals['partial'])
        p,root=self.project();path=root/'.scrum/local/verification-events.jsonl'
        path.write_text('\n'.join(json.dumps(r) for r in [{'at':1,'signature':'same','passed':False,'kind':'missing_report'}, {'at':2,'signature':'same','passed':False,'kind':'missing_report'}, {'at':3,'passed':True,'reused':True}]))
        result=usage_audit.verification_events(root,0,4)
        self.assertEqual((result['executed'],result['reused'],result['repeat_failures']),(2,1,1))
        self.assertEqual(usage_audit.verification_events(root,2.5,4)['events'],1)

    def test_repetitions_report_managed_scope_and_exclude_failed_reads(self):
        import overhead
        p,root=self.project()
        for batch in range(2):
            session=overhead.start_session(p,self.agent,'Light')
            overhead.record_startup(p,self.agent,str(batch),'codex','fixture',session,[7],50,{})
            for task,failed in [(7,False),(7,False),(7,True),(8,False),(8,False)]:
                overhead.record(p,'task_get',10,20,1,failed,session,task,self.agent,channel='cli')
        for _ in range(3):
            overhead.record(p,'task_get',10,20,1,False,task=7,channel='cli')
        events=[dict(at=at,signature='same',passed=True,reused=at==3) for at in (1,2,3)]
        (root/'.scrum/local/verification-events.jsonl').write_text('\n'.join(map(json.dumps,events)))
        result=run('coordination_metrics',dict(project=p,include_audit=True))['audit']
        repeats=result['repetitions']
        self.assertEqual(repeats['claim_reads']['sessions_with_repeated_reads'],2)
        self.assertEqual(repeats['claim_reads']['repeated_reads'],2)
        self.assertEqual(repeats['unchanged_verification_passes'],1)
        self.assertTrue(any('multiple managed sessions' in note for note in result['recommendations']))
        self.assertEqual(overhead.claim_read_repetitions(p,0,1)['repeated_reads'],0)

    def test_concrete_actions_require_exact_recorded_repetition(self):
        import overhead
        p,root=self.project()
        session=overhead.start_session(p,self.agent,'Light')
        overhead.record_startup(p,self.agent,'sample','codex','fixture',session,[7],50,{})
        for args,version in [({},'v1'),({},'v1'),({'files_after':20},'v1'),({},'v2')]:
            signature=overhead.task_read_signature('task_get',args,{'version':version})
            overhead.record(p,'task_get',10,20,1,False,session,7,self.agent,channel='cli',read_signature=signature)
        events=[
            {'at':1,'signature':'stable','passed':True},
            {'at':2,'signature':'stable','passed':True},
            {'at':3,'signature':'stable','passed':True,'reused':True},
            {'at':4,'signature':'different inputs','passed':True},
            {'at':5,'signature':'failure','passed':False,'kind':'missing_report'},
            {'at':6,'signature':'failure','passed':False,'kind':'missing_report'},
            {'at':7,'signature':'other failure','passed':False,'kind':'missing_report'},
            {'at':7,'signature':'other failure','passed':False,'kind':'missing_report'},
        ]
        (root/'.scrum/local/verification-events.jsonl').write_text('\n'.join(map(json.dumps,events)))
        audit=run('coordination_metrics',dict(project=p,include_audit=True))['audit']
        report=audit['repetitions']
        self.assertIn('unknown',report['scope'])
        self.assertEqual(report['claim_reads']['versioned_reads'],4)
        self.assertEqual(report['claim_reads']['identical_versioned_reads'],1)
        patterns={item['kind']:item for item in report['patterns']}
        self.assertEqual(len(report['patterns']),3)
        read=patterns['claim_read']
        passed=patterns['verification_pass']
        failure=patterns['verification_failure']
        self.assertEqual(read['scope']['task_id'],7)
        self.assertEqual(read['action']['destination'],'reuse')
        self.assertEqual(passed['kind'],'verification_pass')
        self.assertEqual(failure['action']['destination'],'helper')
        self.assertEqual((failure['first_at'],failure['last_at']),(5,6))
        self.assertTrue(all(item['observations']==2 for item in report['patterns']))
        self.assertNotIn('different inputs',json.dumps(report))

    def test_exact_delivery_revert_links_evidence_and_creates_one_proposal(self):
        import git_completion
        project,root=self.project()
        def git(*args):
            return subprocess.run(['git','-C',str(root),*args],text=True,encoding='utf-8',
                                  capture_output=True,check=True).stdout.strip()
        git('init','-qb','main')
        git('config','user.name','Revert audit')
        git('config','user.email','fixture@example.invalid')
        for name in ('one.txt','two.txt'):
            (root/name).write_text('base\n',encoding='utf-8')
        git('add','one.txt','two.txt')
        git('commit','-qm','Base')
        def deliver(title,names):
            task=run('task_create',dict(project=project,title=title,description=title,files=names,
                                        acceptance_criteria=['Checked']))
            for name in names:
                (root/name).write_text('delivered\n',encoding='utf-8')
            git('add',*names)
            git('commit','-qm',task['code']+': '+title)
            sha=git('rev-parse','HEAD')
            claim=run('task_claim',dict(project=project,id=task['id'],actor=self.agent))
            run('work_finish',dict(project=project,id=task['id'],actor=self.agent,token=claim['token'],
                                   evidence='Focused check passed.'))
            path=git_completion.location(root,task['id'])
            git_completion.write_receipt(path,{'sha':sha,'target':'main','files':{
                name:git('rev-parse',sha+':'+name) for name in names}})
            return task,sha
        task,delivery=deliver('First verified delivery',['one.txt'])
        git('revert','--no-edit',delivery)
        reverted=git('rev-parse','HEAD')
        partial,partial_sha=deliver('Second verified delivery',['one.txt','two.txt'])
        (root/'one.txt').write_text('base\n',encoding='utf-8')
        git('add','one.txt')
        git('commit','-qm','Partial reversal','-m',f'This reverts commit {partial_sha}.')
        report=run('coordination_metrics',dict(project=project,include_audit=True))['audit']
        patterns=[item for item in report['repetitions']['patterns'] if item['kind']=='git_revert']
        self.assertEqual(len(patterns),1)
        pattern=patterns[0]
        self.assertEqual(pattern['scope']['task_id'],task['id'])
        self.assertEqual(pattern['scope']['delivery_sha'],delivery)
        self.assertEqual(pattern['scope']['revert_sha'],reverted)
        self.assertEqual(pattern['verification_evidence'],'Focused check passed.')
        self.assertNotIn(partial['code'],json.dumps(pattern))
        args=dict(project=project,actor=self.human,proposal_id=pattern['pattern_id'])
        proposal=run('project_knowledge',dict(args,action='learning_propose'))
        self.assertEqual(proposal['scope'],pattern['scope'])
        self.assertEqual(proposal['evidence']['verification_evidence'],pattern['verification_evidence'])
        self.assertEqual(proposal,run('project_knowledge',dict(args,action='learning_propose')))
        reviewed=run('project_knowledge',dict(args,action='learning_review',decision='dismissed'))
        self.assertEqual(reviewed,run('project_knowledge',dict(args,action='learning_propose')))
        self.assertEqual(run('task_get',dict(project=project,id=task['id']))['status'],'Done')
        self.assertFalse((root/'docs').exists())

class NativePublicationTest(unittest.TestCase):
    def test_only_transport_failures_retry_same_refspec(self):
        import git_sync
        failure=Mock(returncode=128,stderr='Could not resolve host: example.invalid')
        success=Mock(returncode=0,stderr='')
        with patch.object(git_sync,'_git',side_effect=[failure,success]) as git:
            result,attempts=git_sync.publish_verified('root','origin','fixedsha:refs/heads/main')
        self.assertEqual(attempts,2);self.assertEqual(result.returncode,0)
        self.assertEqual(git.call_args_list[0],git.call_args_list[1])
        with patch.object(git_sync,'_git',return_value=Mock(returncode=1,stderr='non-fast-forward permission denied')) as git:
            _,attempts=git_sync.publish_verified('root','origin','fixedsha:refs/heads/main')
        self.assertEqual(attempts,1);git.assert_called_once()
