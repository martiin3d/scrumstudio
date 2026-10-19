import unittest
import test_app
import test_storage
from store import run


class ContextTest(unittest.TestCase):
    setUp=test_app.WorkflowTest.setUp
    tearDown=test_app.WorkflowTest.tearDown

    def task(self,title='Bounded fix'):
        return run('task_create',dict(project=self.p,title=title,description='A concrete fix',acceptance_criteria=['Regression passes.']))['id']

    def test_summary_version_and_staleness(self):
        task=self.task()
        run('task_update',dict(project=self.p,id=task,actor=self.actor,continuity='Result: diagnosed. Next: implement fix.'))
        first=run('task_get',dict(project=self.p,id=task))
        self.assertFalse(first['continuity_stale'])
        cached=run('task_get',dict(project=self.p,id=task,version_seen=first['version']))
        self.assertFalse(cached['changed']);self.assertNotIn('description',cached)
        run('task_update',dict(project=self.p,id=task,description='Changed scope'))
        changed=run('task_get',dict(project=self.p,id=task,version_seen=first['version']))
        self.assertTrue(changed['continuity_stale'])
        self.assertNotEqual(first['version'],changed['version'])

    def test_search_filters_pagination_and_literal_wildcards(self):
        ids=[self.task(title) for title in ['100% fixed','Other fix','Another fix']]
        result=run('task_search',dict(project=self.p,query='%',limit=1))
        self.assertEqual([t['id'] for t in result['items']],[ids[0]])
        page=run('task_search',dict(project=self.p,limit=2))
        self.assertTrue(page['has_more'])
        tail=run('task_search',dict(project=self.p,after=page['next_cursor']))
        self.assertEqual([t['id'] for t in tail['items']],[ids[2]])
        self.assertNotIn('description',tail['items'][0])
        self.assertEqual(run('task_search',dict(project=self.q))['items'],[])
        run('task_update',dict(project=self.p,id=ids[0],files=['src/core.py']))
        self.assertEqual(len(run('task_search',dict(project=self.p,file='core.py',status='TODO'))['items']),1)

    def test_incremental_candidate_and_summary_lock(self):
        task=self.task()
        initial=run('next_work',dict(project=self.p,actor=self.actor))['task']
        candidate=run('next_work',dict(project=self.p,actor=self.actor,task_seen=initial['version']))['task']
        self.assertFalse(candidate['changed'])
        run('task_claim',dict(project=self.p,id=task,actor=self.actor))
        with self.assertRaises(ValueError):
            run('task_update',dict(project=self.p,id=task,actor=self.actor,continuity='Cannot bypass token'))

    def test_task_notes_are_scoped_paginated_and_explicit(self):
        task,other=self.task(),self.task('Other')
        base=dict(project=self.p,audience='agent',kind='Review',title='Check',items=['Detailed result'])
        run('note_add',base|{'task':other})
        first=run('task_get',dict(project=self.p,id=task))
        note=run('note_add',base|{'task':task})
        run('note_add',base|{'task':task})
        self.assertNotIn('notes',run('task_get',dict(project=self.p,id=task)))
        self.assertEqual(run('task_get',dict(project=self.p,id=task))['pending_ai_notes'],2)
        refs=run('task_get',dict(project=self.p,id=task,include_notes=True,notes_limit=1))
        self.assertTrue(refs['notes']['has_more'])
        self.assertNotIn('items',refs['notes']['items'][0])
        tail=run('task_get',dict(project=self.p,id=task,include_notes=True,notes_content=True,notes_after=refs['notes']['next_cursor']))
        self.assertEqual(tail['notes']['items'][0]['items'],['Detailed result'])
        run('note_add',base|{'task':task})
        changed=run('task_get',dict(project=self.p,id=task,include_notes=True,version_seen=refs['version']))
        self.assertIn('notes',changed)
        with self.assertRaises(ValueError):run('note_add',base|{'task':999999})
        with self.assertRaises(ValueError):run('task_get',dict(project=self.p,id=task,notes_content=True))

    def test_candidate_ignores_other_task_retrospective(self):
        task,other=self.task(),self.task('Other')
        base=dict(project=self.p,kind='Retrospective',title='Lesson',items=['Keep verified work'])
        global_note=run('note_add',base)['id']
        run('note_add',base|{'task':other})
        self.assertNotIn('retrospective',run('next_work',dict(project=self.p,actor=self.actor)))

    def test_human_notes_excluded_and_completed_ai_reminders_disappear(self):
        task=self.task()
        first=run('task_get',dict(project=self.p,id=task))
        base=dict(project=self.p,task=task,kind='Review',title='Reminder',items=['Remaining integration'])
        run('note_add',base|{'items':['Human explanation only']})
        self.assertFalse(run('task_get',dict(project=self.p,id=task,version_seen=first['version']))['changed'])
        self.assertEqual(run('task_get',dict(project=self.p,id=task,include_notes=True,notes_content=True))['notes']['items'],[])
        note=run('note_add',base|{'audience':'agent'})['id']
        candidate=run('next_work',dict(project=self.p,actor=self.actor))['task']
        self.assertEqual(candidate['pending_ai_notes'],1)
        self.assertNotIn('Remaining integration',str(candidate))
        notes=run('task_get',dict(project=self.p,id=task,include_notes=True,notes_content=True))['notes']['items']
        self.assertEqual([n['items'] for n in notes],[['Remaining integration']])
        run('note_check',dict(project=self.p,id=note,done=True))
        self.assertNotIn('pending_ai_notes',run('task_get',dict(project=self.p,id=task)))
        self.assertEqual(run('task_get',dict(project=self.p,id=task,include_notes=True))['notes']['items'],[])
        self.assertEqual(len(run('task_get',dict(project=self.p,id=task,include_notes=True,notes_audience='all'))['notes']['items']),2)
        with self.assertRaises(ValueError):run('note_add',base|{'audience':'agent','task':None})
        with self.assertRaises(ValueError):run('note_add',base|{'audience':'agent','kind':'Daily'})
        own=run('note_add',base|{'task':task})['id']
        self.assertNotIn('retrospective',run('next_work',dict(project=self.p,actor=self.actor)))


class ContextStorageTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def test_legacy_cleanup_is_scoped_backed_up_and_idempotent(self):
        import json
        project,folder=self.project()
        other,_=self.project('Other')
        task=run('task_create',dict(project=project,title='Keep work',description='Keep verified history',acceptance_criteria=['Preserved']))['id']
        run('task_update',dict(project=project,id=task,actor=self.agent,continuity='Next: integration'))
        base=dict(project=project,title='Old session',items=['Legacy text'])
        for kind in ('Daily','Retrospective'):run('note_add',base|{'kind':kind})
        run('note_add',base|{'kind':'Review','title':'Agent pause handoff'})
        run('note_add',base|{'kind':'Review','title':'Real human follow-up'})
        run('note_add',base|{'kind':'Review','task':task,'audience':'agent'})
        run('note_add',base|{'project':other,'kind':'Daily'})
        with self.assertRaises(ValueError):run('note_cleanup',dict(project=project,actor=self.agent,confirm=True))
        preview=run('note_cleanup',dict(project=project,actor=self.human))
        self.assertEqual((preview['eligible'],preview['deleted']),(3,0))
        self.assertFalse((folder/'.scrum/local/cleanup-backups').exists())
        from unittest.mock import patch
        with patch('store.os.fsync',side_effect=OSError('Backup failed')):
            with self.assertRaises(OSError):run('note_cleanup',dict(project=project,actor=self.human,confirm=True))
        self.assertEqual(run('note_cleanup',dict(project=project,actor=self.human))['eligible'],3)
        cleaned=run('note_cleanup',dict(project=project,actor=self.human,confirm=True))
        from pathlib import Path
        backup=json.loads(Path(cleaned['backup']).read_text(encoding='utf-8'))
        self.assertEqual(len(backup['notes']),3)
        self.assertEqual(cleaned['deleted'],3)
        self.assertEqual(len(run('snapshot',dict(project=project))['notes']),2)
        self.assertEqual(len(run('snapshot',dict(project=other))['notes']),1)
        self.assertEqual(run('task_get',dict(project=project,id=task))['continuity'],'Next: integration')
        again=run('note_cleanup',dict(project=project,actor=self.human,confirm=True))
        self.assertEqual(again['deleted'],0);self.assertNotIn('backup',again)

    def test_summary_survives_portable_clone(self):
        project,folder=self.project()
        task=run('task_create',dict(project=project,title='Portable',description='Portable context',acceptance_criteria=['Restored.']))['id']
        run('task_update',dict(project=project,id=task,actor=self.agent,continuity='Next: verify restore.'))
        run('project_export',dict(project=project))
        cloned,_=self.clone(folder)
        self.assertEqual(run('task_get',dict(project=cloned['id'],code='PRO-1'))['continuity'],'Next: verify restore.')

    def test_linked_and_legacy_notes_survive_clone(self):
        import json
        project,folder=self.project()
        task=run('task_create',dict(project=project,title='Linked',description='Portable reference',acceptance_criteria=['Restored']))['id']
        run('note_add',dict(project=project,task=task,audience='agent',kind='Review',title='Result',items=['Saved once']))
        run('project_export',dict(project=project))
        cloned,_=self.clone(folder)
        restored=run('task_get',dict(project=cloned['id'],code='PRO-1',include_notes=True,notes_content=True))
        self.assertEqual(restored['notes']['items'][0]['items'],['Saved once'])
        path=folder/'.scrum/snapshot.json'
        data=json.loads(path.read_text(encoding='utf-8'))
        # Older exports have no task column at all.
        data['tables']['notes'][0].pop('task')
        data['tables']['notes'][0].pop('audience')
        path.write_text(json.dumps(data),encoding='utf-8')
        legacy,_=self.clone(folder)
        self.assertIsNone(run('snapshot',dict(project=legacy['id']))['notes'][0]['task'])

