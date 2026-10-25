"""Opt-in local before/after measurements; no models, provider polling or task edits."""
import json
import math
import os
import time
import uuid
from pathlib import Path
from run_cost import atomic
from contextlib import contextmanager
from functools import wraps


def location(root):
    return Path(root)/'.scrum/local/usage-measurement.json'


def read(root):
    path=location(root)
    try:
        if path.is_symlink() or path.stat().st_size>1024*1024:return None
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None


@contextmanager
def locked(root):
    path=location(root).with_suffix('.lock');path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if path.stat().st_size==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        # Native advisory lock serializes competing supervisors; no model call.
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def serialized(function):
    @wraps(function)
    def wrapped(root,*args,**kwargs):
        if function.__name__!='begin':
            state=read(root)
            if not state or state.get('status')!='recording':return None
        with locked(root):return function(root,*args,**kwargs)
    return wrapped


def fresh(value,at=None):
    at=time.time() if at is None else at
    return (isinstance(value,dict) and type(value.get('observed_at')) in (int,float)
            and math.isfinite(value['observed_at']) and 0<=at-value['observed_at']<=300
            and all(type(value.get(k)) in (int,float) and math.isfinite(value[k]) and 0<=value[k]<=100
                    for k in ('session_used_percent','weekly_used_percent')))


@serialized
def begin(root,project,quota,audit):
    if not fresh(quota):raise ValueError('A fresh real provider reading of both quota windows is required before launch')
    old=read(root)
    if old and old.get('status')=='recording':raise ValueError('An existing measurement is recording; finish it before replacing its baseline')
    state={'schema':1,'id':uuid.uuid4().hex,'project':project,'status':'recording','started_at':time.time(),
           'client':'codex','baseline_usage':quota,'latest_usage':quota,'baseline_audit':audit,'runs':{},
           'quota_scope':'Shared provider account: includes this chat, other projects and devices; not attributable solely to this project.'}
    atomic(location(root),state)
    return summary(root)


@serialized
def observe(root,quota,client='codex'):
    state=read(root)
    if not state or state.get('status')!='recording' or state.get('client','codex')!=client or not fresh(quota):return
    if quota['observed_at']<=state.get('latest_usage',{}).get('observed_at',0):return
    state['latest_usage']=quota;atomic(location(root),state)


@serialized
def record_run(root,report):
    state=read(root)
    if not state or state.get('status')!='recording' or not report:return
    # Whitelist telemetry only; never retain prompts, command output or secrets.
    state['runs'][report['run_id']]={k:report.get(k) for k in ('run_id','model','tasks','source','input_tokens','cached_input_tokens','output_tokens')}
    atomic(location(root),state)


@serialized
def finish(root,reason,rpc,project,client=None):
    state=read(root)
    if not state or state.get('status')!='recording':return None
    if client is not None and state.get('client','codex')!=client:return None
    ended=time.time()
    try:
        result=rpc.call('coordination_metrics',{'project':project,'include_audit':True,'include_logs':False,'start_at':state['started_at'],'end_at':ended})
        state['interval_audit']=result['audit']
    except (OSError,RuntimeError,ValueError,KeyError) as error:state['snapshot_error']=str(error)[:300]
    # The last native reading may precede the terminal event. Keep its timestamp
    # rather than relabeling it as a fresh read or launching a provider query.
    state.update(status='ended',ended_at=ended,reason=reason)
    atomic(location(root),state)
    report=location(root).parent/'measurements'/(state['id']+'.json')
    atomic(report,state)
    return summary(root)


def summary(root):
    state=read(root)
    if not state:return None
    base=state.get('baseline_usage',{});latest=state.get('latest_usage',{});delta={}
    for prefix in ('session','weekly'):
        reset=prefix+'_resets_at';key=prefix+'_used_percent'
        comparable=(base.get(reset) is not None and base.get(reset)==latest.get(reset)
                    and fresh(latest,state.get('ended_at')) and latest.get(key,-1)>=base.get(key,0))
        delta[prefix]=round(latest[key]-base[key],3) if comparable else None
    audit=state.get('interval_audit',{})
    return {k:state.get(k) for k in ('id','client','status','started_at','ended_at','reason','quota_scope','snapshot_error')}|{
        'baseline_usage':base,'latest_usage':latest,'account_delta_percentage_points':delta,
        'recorded_interval':audit.get('recorded'),'flow':audit.get('flow'),
        'recorded_runs':len(state.get('runs',{})),
        'report':'.scrum/local/measurements/'+state['id']+'.json' if state.get('status')=='ended' else '.scrum/local/usage-measurement.json',
        'basis':'Native readings with original timestamps. Reset/stale windows have no comparable quota delta. Project tokens cover reported completed executions only; unknown/in-flight data is not zero.'}
