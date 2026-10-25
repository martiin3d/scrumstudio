"""Incremental local CLI counters and one advisory at an existing MCP boundary."""
import json
import os
import time
from pathlib import Path

DEFAULTS={'run_advisory_minutes':20,'run_advisory_tools':60,'run_advisory_output_tokens':30000}


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,separators=(',',':')),encoding='utf-8')
    os.replace(temporary,path)


class Watch:
    def __init__(self,log,offset,target,run_id,tasks,policy,client,clock=time.monotonic):
        self.log=Path(log);self.offset=offset;self.target=Path(target);self.clock=clock;self.started=clock()
        self.policy={k:policy.get(k,v) for k,v in DEFAULTS.items()}
        self.state={'run_id':run_id,'tasks':tasks,'client':client,'tools':0,'output_tokens':None,'input_tokens':None,'cached_input_tokens':None,'limited':False}
        self.tick()

    def tick(self,active=True):
        # Each byte is examined once, at the supervisor's existing checkpoints.
        try:
            with self.log.open('rb') as stream:
                stream.seek(self.offset);budget=4*1024*1024
                while budget>0:
                    begin=stream.tell();line=stream.readline(min(budget,1024*1024)+1)
                    if not line:break
                    if not line.endswith(b'\n'):
                        # Wait for a complete event; skip oversized lines boundedly.
                        if len(line)>1024*1024:self.state['limited']=True;self.offset=stream.tell()
                        break
                    self.offset=stream.tell();budget-=len(line)
                    try:e=json.loads(line)
                    except ValueError:continue
                    if not isinstance(e,dict):continue
                    item=e.get('item') or {}
                    if not isinstance(item,dict):item={}
                    if e.get('type')=='item.completed' and item.get('type') in ('command_execution','mcp_tool_call','web_search'):
                        self.state['tools']+=1
                    if e.get('type')=='assistant':
                        message=e.get('message') or {}
                        if isinstance(message,dict) and isinstance(message.get('content'),list):
                            self.state['tools']+=sum(isinstance(x,dict) and x.get('type')=='tool_use' for x in message['content'])
                    if e.get('type') in ('turn.completed','result') and isinstance(e.get('usage'),dict):
                        for key,field in [('input_tokens','input_tokens'),('output_tokens','output_tokens'),('cached_input_tokens','cache_read_input_tokens' if self.state['client']=='claude' else 'cached_input_tokens')]:
                            n=e['usage'].get(field)
                            if type(n) is int and n>=0:self.state[key]=n
                if budget<=0:self.state['limited']=True
        except OSError:self.state['limited']=True
        seconds=max(0,self.clock()-self.started);reasons=[]
        for key,value in [('run_advisory_minutes',seconds/60),('run_advisory_tools',self.state['tools']),('run_advisory_output_tokens',self.state['output_tokens'])]:
            threshold=self.policy[key]
            if threshold and value is not None and value>=threshold:reasons.append(key)
        self.state.update(elapsed_seconds=round(seconds),active=active,observed_at=time.time(),advisory=reasons,thresholds=self.policy)
        try:atomic(self.target,self.state)
        except OSError:pass
        return self.state


def notice(path):
    """Deliver once per execution; no polling, interruption or automatic relaunch."""
    if not path:return None
    try:
        path=Path(path);state=json.loads(path.read_text(encoding='utf-8'))
        if not state.get('active') or not state.get('advisory') or not 0<=time.time()-state['observed_at']<=180:return None
        ack=path.with_suffix('.ack')
        if ack.exists() and ack.read_text(encoding='utf-8')==state['run_id']:return None
        ack.write_text(state['run_id'],encoding='utf-8')
        return {'elapsed_seconds':state['elapsed_seconds'],'tools':state['tools'],'output_tokens':state['output_tokens'],
                'instruction':'Cost advisory: stop broad exploration and duplicate evidence. Finish the smallest remaining acceptance checks using saved results; expand only for an actual failure or mandatory criterion. Save a concise finding/next step in the next necessary task update. Do not exit, split tasks, relaunch, weaken acceptance or mark Blocked solely because of this advisory.'}
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return None


def current(root):
    result=[]
    for path in sorted((Path(root)/'.scrum/local/run-cost').glob('*.json'))[:20]:
        try:
            if path.is_symlink() or path.stat().st_size>10000:continue
            state=json.loads(path.read_text(encoding='utf-8'))
            result.append({k:state.get(k) for k in ('run_id','tasks','client','tools','elapsed_seconds','input_tokens','cached_input_tokens','output_tokens','active','observed_at','advisory','limited','thresholds')})
        except (OSError,ValueError,AttributeError):continue
    return result
