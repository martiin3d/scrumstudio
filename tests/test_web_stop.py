import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
import urllib.error
import web_lifecycle as lifecycle

ROOT=Path(__file__).resolve().parents[1]

class WebStopTest(unittest.TestCase):
    def setUp(self):self.instances=[]
    def tearDown(self):
        for process,port in self.instances:
            if process.poll() is None:
                process.terminate();process.wait(timeout=5)
            lifecycle.control_path(port).unlink(missing_ok=True)
    def free_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
    def start(self):
        port=self.free_port()
        process=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,env=dict(os.environ,SCRUM_PORT=str(port)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.instances.append((process,port))
        for _ in range(50):
            if process.poll() is not None:self.fail('Test website exited before readiness')
            if lifecycle.control_path(port).exists():return process,port
            time.sleep(.05)
        self.fail('Test website not ready')
    def request(self,port,token,origin=None):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        request=urllib.request.Request(f'http://127.0.0.1:{port}/api/_shutdown',data=json.dumps({'token':token}).encode(),headers=headers)
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=2)
    def test_rejected_requests_and_independent_instances(self):
        first,port=self.start();second,other=self.start()
        record=lifecycle.read_control(port)
        for token,origin in [('invalid',None),(record['token'],'https://example.com')]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.request(port,token,origin)
            self.assertEqual(error.exception.code,403)
            self.assertIsNone(first.poll())
        result=subprocess.run([sys.executable,str(ROOT/'scripts/stop.py')],cwd=ROOT,env=dict(os.environ,SCRUM_PORT=str(port)),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(first.wait(timeout=5),0)
        self.assertIsNone(second.poll())
        self.assertFalse(lifecycle.control_path(port).exists())
        self.assertEqual(lifecycle.stop(port),0)
        self.assertEqual(lifecycle.stop(other),0)
        self.assertEqual(second.wait(timeout=5),0)
    def test_stale_record_and_wrong_checkout_do_not_kill(self):
        port=self.free_port();token=lifecycle.publish(port)
        self.addCleanup(lambda:lifecycle.control_path(port).unlink(missing_ok=True))
        with self.assertRaisesRegex(ValueError,'no process was killed'):lifecycle.stop(port)
        record=lifecycle.read_control(port);record['root']='different-checkout'
        lifecycle.control_path(port).write_text(json.dumps(record),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Invalid website control'):lifecycle.stop(port)
    def test_cleanup_does_not_remove_new_instance_record(self):
        port=self.free_port();old=lifecycle.publish(port);new=lifecycle.publish(port)
        self.addCleanup(lambda:lifecycle.control_path(port).unlink(missing_ok=True))
        lifecycle.clear(port,old)
        self.assertEqual(lifecycle.read_control(port)['token'],new)
        lifecycle.clear(port,new)
        self.assertIsNone(lifecycle.read_control(port))
