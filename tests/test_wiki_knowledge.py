import json
import subprocess
from pathlib import Path
import tempfile
import unittest

from store import run
from test_storage import StorageTest
import wiki_knowledge


class WikiDoctorTest(unittest.TestCase):
    def test_reports_broken_links_anchors_orphans_and_revision_without_reading_examples(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            root=Path(folder)
            wiki=root/wiki_knowledge.WIKI
            wiki.mkdir(parents=True)
            (root/'AGENTS.md').write_text('[Map](docs/agents/index.md)\n',encoding='utf-8')
            (wiki/'index.md').write_text('[Topic](topic.md#valid-anchor)\n[Broken](missing.md)\n',encoding='utf-8')
            (wiki/'topic.md').write_text(
                '## Valid `anchor`\n[Bad anchor](#obsolete)\n[Missing SHA](git:unknown-revision)\n'
                '```markdown\n[example](not-real.md)\n```\n',
                encoding='utf-8')
            (wiki/'orphan.md').write_text('# Orphan\n',encoding='utf-8')
            issues=wiki_knowledge.diagnose(root)
            self.assertEqual({(row['kind'],row['target']) for row in issues},{
                ('broken_link','missing.md'),('missing_anchor','#obsolete'),
                ('unresolved_git_reference','git:unknown-revision'),('unindexed_page','docs/agents/index.md'),
            })

    def test_valid_commit_reference_is_resolved_locally(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            root=Path(folder)
            def git(*args):
                return subprocess.check_output(['git','-C',folder,*args],text=True).strip()
            git('init','-q')
            git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','base')
            self.assertTrue(wiki_knowledge.git_reference(root,git('rev-parse','HEAD')))
            self.assertFalse(wiki_knowledge.git_reference(root,'--help'))


class WikiProposalTest(unittest.TestCase):
    setUp=StorageTest.setUp
    tearDown=StorageTest.tearDown
    project=StorageTest.project

    def test_confirmed_failure_becomes_one_reviewable_learning_record(self):
        project,root=self.project()
        wiki=root/wiki_knowledge.WIKI
        wiki.mkdir(parents=True)
        index=wiki/'index.md'
        index.write_text('# Project knowledge\n',encoding='utf-8')
        task=run('task_create',dict(project=project,title='Unrelated work',description='Keep this task',
                                    acceptance_criteria=['No change']))
        evidence=root/'.scrum/local/verification-events.jsonl'
        events=[
            {'at':1,'signature':'matching inputs','passed':False,'kind':'missing_report'},
            {'at':2,'signature':'matching inputs','passed':False,'kind':'missing_report'},
            {'at':3,'signature':'different inputs','passed':False,'kind':'missing_report'},
        ]
        evidence.write_text('\n'.join(map(json.dumps,events)),encoding='utf-8')
        audit=run('coordination_metrics',dict(project=project,include_audit=True))['audit']
        pattern=audit['repetitions']['patterns'][0]
        args={'project':project,'proposal_id':pattern['pattern_id']}
        with self.assertRaisesRegex(ValueError,'Only a human'):
            run('project_knowledge',dict(args,actor=self.agent,action='learning_propose'))
        with self.assertRaisesRegex(ValueError,'No confirmed'):
            run('project_knowledge',dict(args,actor=self.human,action='learning_propose',proposal_id='0'*24))
        pending=run('project_knowledge',dict(args,actor=self.human,action='learning_propose'))
        self.assertEqual(pending['status'],'pending')
        self.assertEqual(pending['recommendation']['destination'],'helper')
        self.assertEqual(pending['evidence']['pattern_id'],pattern['pattern_id'])
        self.assertEqual(pending['scope'],pattern['scope'])
        self.assertEqual(pending,run('project_knowledge',dict(args,actor=self.human,action='learning_propose')))
        self.assertEqual(evidence.read_text(encoding='utf-8'),'\n'.join(map(json.dumps,events)))
        evidence.write_text('',encoding='utf-8')
        accepted=run('project_knowledge',dict(args,actor=self.human,action='learning_review',decision='accepted'))
        self.assertEqual(accepted['evidence'],pending['evidence'])
        self.assertEqual(accepted['status'],'accepted')
        self.assertEqual(accepted,run('project_knowledge',dict(args,actor=self.human,action='learning_propose')))
        with self.assertRaisesRegex(ValueError,'already been reviewed'):
            run('project_knowledge',dict(args,actor=self.human,action='learning_review',decision='dismissed'))
        self.assertEqual(run('project_knowledge',dict(project=project,action='learning'))['items'],[accepted])
        self.assertEqual(run('project_knowledge',dict(project=project,action='view'))['learning']['items'],[accepted])
        self.assertEqual(run('task_get',dict(project=project,id=task['id']))['status'],task['status'])
        self.assertEqual(index.read_text(encoding='utf-8'),'# Project knowledge\n')

    def test_work_finish_suggests_once_and_keeps_evidence_in_the_source_task(self):
        project,root=self.project()
        wiki=root/wiki_knowledge.WIKI
        wiki.mkdir(parents=True)
        (wiki/'index.md').write_text('[Git](git.md)\n',encoding='utf-8')
        page=wiki/'git.md'
        original='# Git\n'
        page.write_text(original,encoding='utf-8')
        task=run('task_create',dict(project=project,title='Keep Git staging',description='Recover safely',
                                    files=['git_sync.py'],acceptance_criteria=['Staging preserved']))
        claim=run('task_claim',dict(project=project,id=task['id'],actor=self.agent))
        result=run('work_finish',dict(project=project,id=task['id'],actor=self.agent,token=claim['token'],
                    evidence='Failed commit staging regression passed.',
                    decisions=[dict(key='keep-staging',decision='Preserve the original Git index.',
                                    reason='Staging can belong to the user.',files=['git_sync.py'])]))
        self.assertEqual(result['wiki_proposals'][0]['page'],'docs/agents/git.md')
        listing=dict(project=project,id=task['id'])
        proposal=run('project_wiki_proposals',listing)['items'][0]
        self.assertEqual(proposal['source_task'],task['id'])
        self.assertEqual(proposal['source_code'],task['code'])
        self.assertEqual(proposal['evidence_excerpt'],'Failed commit staging regression passed.')
        self.assertEqual(page.read_text(encoding='utf-8'),original)
        page.write_text(original+proposal['draft_markdown'],encoding='utf-8')
        self.assertEqual(run('project_wiki_proposals',listing)['items'],[])

    def test_proposals_wait_for_done_and_reject_unsafe_page_names(self):
        project,root=self.project()
        wiki=root/wiki_knowledge.WIKI
        wiki.mkdir(parents=True)
        (wiki/'index.md').write_text('[Notes](notes.md)\n',encoding='utf-8')
        (wiki/'notes.md').write_text('# Notes\n',encoding='utf-8')
        task=run('task_create',dict(project=project,title='Record procedure',description='Reusable notes',
                                    acceptance_criteria=['Procedure verified']))
        record=dict(key='usable-procedure',decision='Use the scoped procedure.',reason='It can be reproduced.',
                    files=['other.py'],wiki_page='docs/agents/../outside.md')
        request=dict(project=project,id=task['id'],actor=self.human,status='In Review',evidence='Verified')
        with self.assertRaisesRegex(ValueError,'topic page'):
            run('task_update',dict(request,decisions=[record]))
        record['wiki_page']='docs/agents/notes.md'
        run('task_update',dict(request,decisions=[record]))
        self.assertEqual(run('project_wiki_proposals',dict(project=project))['items'],[])
        run('task_review',dict(project=project,id=task['id'],actor=self.human,decision='approve'))
        self.assertEqual(run('project_wiki_proposals',dict(project=project))['items'][0]['page'],'docs/agents/notes.md')
