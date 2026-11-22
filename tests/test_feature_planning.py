import base64
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid
from urllib.request import Request, urlopen

from desktop import desktop_backend
from feature_planning import PRIVATE
from scripts import feature_planner
from store import run
import test_storage


def task(key='feature', dependencies=None):
    return {'key':key,'title':f'Deliver {key}','description':'Implement one independently verifiable outcome.',
            'acceptance_criteria':['The outcome is verified.'],'files':[],'dependencies':dependencies or [],
            'scope_size':'Small','scope_reason':'One outcome','required_skills':[],'specification':None}


class FeaturePlanningTest(unittest.TestCase):
    setUp=test_storage.StorageTest.setUp
    tearDown=test_storage.StorageTest.tearDown
    project=test_storage.StorageTest.project

    def setUpRequest(self):
        self.p,self.folder=self.project()
        self.context={'project':self.p,'id':uuid.uuid4().hex}

    def call(self,action,**kwargs):
        return run('feature_planning',{**self.context,'action':action,**kwargs})

    def submit(self):
        return self.call('submit',actor=self.human,text='Support this feature')

    @patch('feature_planning.launch')
    def test_shared_http_backend_accepts_feature_upload(self,launch):
        self.setUpRequest()
        content=base64.b64encode(b'a'*100_000).decode('ascii')
        payload={**self.context,'action':'submit','actor':self.human,'text':'Support this feature',
                 'attachments':[{'name':'reference.txt','content_base64':content}]}
        with desktop_backend(0) as url:
            for client in ('','?desktop=1'):
                with urlopen(url+'/'+client,timeout=3) as response:
                    self.assertIn(b'app.js',response.read())
            request=Request(url+'/api/feature_planning',data=json.dumps(payload).encode(),
                            headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=3) as response:
                self.assertEqual(json.load(response)['state'],'queued')
        self.assertEqual(self.call('list')['requests'][0]['attachments'][0]['size'],100_000)

    @patch('feature_planning.launch')
    def test_approval_creates_native_linked_tasks_once(self,launch):
        self.setUpRequest()
        self.assertEqual(self.submit()['state'],'queued')
        self.submit()
        launch.assert_called_once()
        with self.assertRaisesRegex(ValueError,'Internal planner'):
            self.call('begin',actor=self.agent,attempt=1,capability='guess')
        self.call('begin',actor=self.agent,attempt=1,capability=PRIVATE)
        self.call('finish',attempt=1,capability=PRIVATE,draft={'message':'Two outcomes','question':'','items':[task('first'),task('second',['first'])]})
        self.assertEqual(run('snapshot',{'project':self.p})['tasks'],[])
        approved=self.call('apply',actor=self.human,attempt=1)
        self.assertEqual(self.call('apply',actor=self.human,attempt=1)['tasks'],approved['tasks'])
        first,second=approved['tasks']
        self.assertEqual(run('task_get',{'project':self.p,'id':second['id']})['dependencies'],[first['id']])
        self.assertEqual(run('task_get',{'project':self.p,'id':second['id']})['status'],'TODO')

    @patch('feature_planning.launch')
    def test_clarifications_survive_retry_and_stop_prevents_work(self,launch):
        self.setUpRequest()
        self.submit()
        self.call('begin',actor=self.agent,attempt=1,capability=PRIVATE)
        result=self.call('finish',attempt=1,capability=PRIVATE,draft={'message':'One detail','question':'Who is it for?','items':[]})
        self.assertEqual(result['state'],'question')
        self.assertEqual(self.call('answer',actor=self.human,attempt=1,text='For reviewers')['attempt'],2)
        run('project_control',{'project':self.p,'actor':self.human,'action':'stop'})
        with self.assertRaisesRegex(ValueError,'Stop work'):
            self.call('begin',actor=self.agent,attempt=2,capability=PRIVATE)
        result=self.call('finish',attempt=2,capability=PRIVATE,error='The project is stopped')
        self.assertIn('For reviewers',result['answer'])
        self.assertEqual(self.call('retry',actor=self.human,attempt=2)['attempt'],3)
        with self.assertRaisesRegex(ValueError,'superseded'):
            self.call('finish',attempt=2,capability=PRIVATE,error='Stale output')

    @patch('feature_planning.launch')
    def test_planner_reserves_the_existing_project_capacity(self,launch):
        self.setUpRequest()
        run('project_settings',{'project':self.p,'actor':self.human,'max_parallel_agents':1})
        self.submit()
        self.call('begin',actor=self.agent,attempt=1,capability=PRIVATE)
        other=run('user_register',{'identity':'agent:another','name':'Another','kind':'agent'})['id']
        run('agent_checkpoint',{'project':self.p,'actor':other,'client':'codex','session_used_percent':1,'weekly_used_percent':1})
        candidate=run('task_create',{'project':self.p,'actor':self.human,'title':'Regular task','description':'Work after planning','acceptance_criteria':['Done']})
        with self.assertRaisesRegex(ValueError,'parallel-agent limit'):
            run('task_claim',{'project':self.p,'id':candidate['id'],'actor':other})

    @patch('feature_planning.launch')
    def test_invalid_dependency_keeps_the_request_ready_for_revision(self,launch):
        self.setUpRequest()
        self.submit()
        self.call('begin',actor=self.agent,attempt=1,capability=PRIVATE)
        self.call('finish',attempt=1,capability=PRIVATE,draft={'message':'Draft','question':'','items':[task('first',['missing'])]})
        with self.assertRaises(ValueError):
            self.call('apply',actor=self.human,attempt=1)
        self.assertEqual(run('snapshot',{'project':self.p})['tasks'],[])
        self.assertEqual(run('feature_planning',{'project':self.p})['requests'][0]['state'],'ready')
        self.assertEqual(self.call('answer',actor=self.human,attempt=1,text='Remove the missing dependency')['state'],'queued')

    @patch('feature_planning.launch')
    def test_fake_codex_run_records_usage_and_only_proposes(self,launch):
        self.setUpRequest()
        upload={'name':'requirements.txt','content_base64':'aGVsbG8='}
        with self.assertRaisesRegex(ValueError,'base64'):
            self.call('submit',actor=self.human,text='Support this feature',
                      attachments=[upload,dict(upload,content_base64='!!!!')])
        self.assertEqual(self.call('list')['requests'],[])
        values={'actor':self.human,'text':'Support this feature','attachments':[upload]}
        self.call('submit',**values)
        self.call('submit',**values)
        launch.assert_called_once()
        with self.assertRaisesRegex(ValueError,'different attachments'):
            self.call('submit',**dict(values,attachments=[]))
        self.call('finish',attempt=1,capability=PRIVATE,error='Planner unavailable')
        self.call('retry',actor=self.human,attempt=1)

        def spawn(argv, **kwargs):
            self.assertIn('read-only',argv)
            self.assertIn('approval_policy="never"',argv)
            self.assertIn('mcp_servers.scrum-studio.enabled=false',argv)
            payload=json.loads(kwargs['stdin'].read().decode('utf-8').split('\n',1)[1])
            reference,=payload['attachments']
            self.assertEqual(reference['name'],'requirements.txt')
            self.assertEqual((Path(kwargs['cwd'])/reference['path']).read_bytes(),b'hello')
            output=Path(argv[argv.index('--output-last-message')+1])
            output.write_text(json.dumps({'message':'Draft','question':'','items':[task()]}),encoding='utf-8')
            kwargs['stdout'].write(b'{"type":"turn.completed","usage":{"input_tokens":50,"output_tokens":10,"cached_input_tokens":20}}\n')
            kwargs['stdout'].flush()
            return Mock(poll=Mock(return_value=0),returncode=0)

        with patch.object(feature_planner,'codex_usage',return_value={'session_used_percent':1,'weekly_used_percent':1}), \
             patch.object(feature_planner,'codex_executable',return_value='codex'), \
             patch.object(feature_planner,'discover',return_value=({},[])), \
             patch.object(feature_planner.subprocess,'Popen',side_effect=spawn):
            feature_planner.main(self.p,self.context['id'],2)
        latest=run('feature_planning',{'project':self.p})['requests'][0]
        self.assertEqual(latest['state'],'ready')
        self.assertEqual(latest['attachments'],[{'name':'requirements.txt','mime':'text/plain','size':5}])
        self.assertEqual(run('snapshot',{'project':self.p})['tasks'],[])
        usage=run('coordination_metrics',{'project':self.p,'include_cli_usage':True})['cli_usage']
        self.assertEqual((usage['input_tokens'],usage['output_tokens']),(50,10))
