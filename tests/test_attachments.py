import base64
import json
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
import test_storage
from store import run
import attachments

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nm5sAAAAASUVORK5CYII=')


class AttachmentTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project
    clone=test_storage.StorageTest.clone

    def task(self,p):
        return run('task_create',dict(project=p,title='Reference fixture',description='Use explicit task references',acceptance_criteria=['References stay available']))['id']

    def call(self,p,task,action='list',**fields):
        return run('task_attachment',dict(project=p,id=task,action=action,**fields))

    def add(self,p,task,**fields):
        return self.call(p,task,'add',**dict(actor=self.human,name='reference.png',content_base64=base64.b64encode(PNG).decode(),**fields))

    def test_metadata_only_content_consistency_lifecycle_and_remove(self):
        p,path=self.project();task=self.task(p)
        added=self.add(p,task)
        self.assertTrue(added['previewable']);self.assertEqual(added['mime'],'image/png')
        self.assertNotIn('content_base64',added)
        self.assertEqual(run('task_get',dict(project=p,id=task))['attachment_count'],1)
        self.assertEqual(self.call(p,task)['items'][0]['id'],added['id'])
        self.assertEqual(run('task_get',dict(project=p,id=task,full=True))['attachments'][0]['id'],added['id'])
        self.assertNotIn('content_base64',self.call(p,task,'get',attachment_id=added['id']))
        fetched=self.call(p,task,'get',attachment_id=added['id'],include_content=True)
        self.assertEqual(base64.b64decode(fetched['content_base64']),PNG)
        run('task_update',dict(project=p,id=task,actor=self.human,owner_id=self.agent,status='In Review'))
        run('task_update',dict(project=p,id=task,actor=self.human,status='Done',evidence='Fixture verified'))
        self.assertTrue(self.call(p,task)['items'][0]['available'])
        blob=path/'.scrum/local/attachments'/(added['id']+'.bin')
        self.assertTrue(blob.is_file())
        self.call(p,task,'remove',actor=self.human,attachment_id=added['id'])
        self.assertFalse(blob.exists());self.assertEqual(self.call(p,task)['items'],[])

    def test_task_and_project_isolation_and_active_claim_credentials(self):
        p,_=self.project('One');q,_=self.project('Two')
        task=self.task(p);other=self.task(p);foreign=self.task(q)
        claim=run('task_claim',dict(project=p,id=task,actor=self.agent))
        with self.assertRaises(ValueError):self.add(p,task)
        added=self.call(p,task,'add',actor=self.agent,token=claim['token'],name='notes.txt',content_base64=base64.b64encode(b'Reference text').decode())
        for target,item in [(p,other),(q,foreign)]:
            with self.assertRaises(ValueError):self.call(target,item,'get',attachment_id=added['id'],include_content=True)
        with self.assertRaises(ValueError):self.call(p,task,'remove',actor=self.human,attachment_id=added['id'])
        run('task_update',dict(project=p,id=task,actor=self.agent,token=claim['token'],status='TODO'))
        self.assertEqual(self.call(p,task)['items'][0]['name'],'notes.txt')

    def test_invalid_names_types_payloads_ids_and_limits(self):
        p,_=self.project();task=self.task(p)
        for name in ('../secret.txt','C:\\secret.txt','file.html','script.svg','macro.exe','bad\nname.txt'):
            with self.assertRaises(ValueError):self.call(p,task,'add',actor=self.human,name=name,content_base64='YQ==')
        for content in ('not base64','',base64.b64encode(b'not an image').decode()):
            with self.assertRaises(ValueError):self.call(p,task,'add',actor=self.human,name='bad.png',content_base64=content)
        with self.assertRaises(ValueError):self.call(p,task,'add',actor=self.human,name='large.txt',content_base64='A'*(4*((attachments.MAX_BYTES+2)//3)+1))
        with self.assertRaises(ValueError):self.call(p,task,'get',attachment_id='../../secret',include_content=True)
        with self.assertRaises(ValueError):self.call(p,task,'get',path=str(self.root/'registry.db'))
        for number in range(attachments.MAX_FILES):self.call(p,task,'add',actor=self.human,name=f'{number}.txt',content_base64='YQ==')
        with self.assertRaises(ValueError):self.call(p,task,'add',actor=self.human,name='eleven.txt',content_base64='YQ==')
        self.assertEqual(len(self.call(p,task)['items']),attachments.MAX_FILES)

    def test_export_metadata_only_and_import_reports_missing_content(self):
        p,path=self.project();task=self.task(p);added=self.add(p,task)
        exported=run('project_export',dict(project=p))
        self.assertFalse(exported['attachments']['content_included'])
        self.assertEqual(exported['attachments']['count'],1)
        clone,_=self.clone(path)
        copied=run('task_get',dict(project=clone['id'],code=run('task_get',dict(project=p,id=task))['code']))
        self.assertEqual(copied['attachment_count'],1)
        self.assertEqual(self.call(clone['id'],copied['id'])['items'][0]['id'],added['id'])
        self.assertFalse(self.call(clone['id'],copied['id'])['items'][0]['available'])
        with self.assertRaisesRegex(ValueError,'content is missing'):
            self.call(clone['id'],copied['id'],'get',attachment_id=added['id'],include_content=True)
        self.assertTrue(self.call(p,task)['items'][0]['available'])

    def test_http_upload_larger_than_normal_json_limit(self):
        from server import Handler
        p,_=self.project();task=self.task(p)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            content=base64.b64encode(b'a'*120000).decode()
            body=json.dumps(dict(project=p,id=task,actor=self.human,action='add',name='large-reference.txt',content_base64=content)).encode()
            request=Request(f'http://127.0.0.1:{server.server_port}/api/task_attachment',data=body,headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=5) as response:result=json.load(response)
            self.assertEqual(result['size'],120000)
            self.assertNotIn('content_base64',result)
        finally:server.shutdown();server.server_close();thread.join()
