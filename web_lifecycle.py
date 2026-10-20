"""Per-checkout website lifecycle. Never signals arbitrary PIDs."""
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parent

def control_path(port):return ROOT/'data'/f'web-{port}.json'

def read_control(port):
    path=control_path(port)
    if not path.exists():return None
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('root')!=str(ROOT) or value.get('port')!=port or not isinstance(value.get('token'),str) or len(value['token'])!=64:
        raise ValueError('Invalid website control record; refusing shutdown')
    return value

def publish(port):
    path=control_path(port);path.parent.mkdir(parents=True,exist_ok=True)
    record={'root':str(ROOT),'port':port,'token':secrets.token_hex(32)}
    fd,temp=tempfile.mkstemp(prefix='web-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(record,stream)
        os.replace(temp,path)
    finally:
        if Path(temp).exists():Path(temp).unlink()
    return record['token']

def clear(port,token):
    record=read_control(port)
    if record and secrets.compare_digest(record['token'],token):control_path(port).unlink(missing_ok=True)

def stop(port):
    if not 1<=port<=65535:raise ValueError('Invalid SCRUM_PORT')
    record=read_control(port)
    if record is None:
        print('No managed website instance recorded for this checkout and port.')
        return 0
    request=urllib.request.Request(f'http://127.0.0.1:{port}/api/_shutdown',data=json.dumps({'token':record['token']}).encode(),headers={'Content-Type':'application/json'},method='POST')
    # Ignore system HTTP proxies for this strictly loopback request.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request,timeout=3) as response:
            if json.load(response).get('stopping') is not True:raise ValueError('Unexpected shutdown response')
    except OSError:
        raise ValueError('Website did not accept shutdown; no process was killed. It may be stopped, stale or a different service.') from None
    for _ in range(150):
        current=read_control(port)
        if current is None or current['token']!=record['token']:
            print('Scrum Studio website stopped. MCP and project data are unchanged.')
            return 0
        time.sleep(.1)
    raise ValueError('Shutdown requested but not confirmed; no process was force-killed')
