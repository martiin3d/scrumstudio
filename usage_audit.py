"""On-demand native usage audit. No model, provider requests or raw log export."""
import collections
import json
import time
from pathlib import Path

COUNTERS=('input_tokens','cached_input_tokens','output_tokens')

def totals(rows):
    known=[r for r in rows if type(r.get('input_tokens')) is int and type(r.get('output_tokens')) is int]
    values={k:sum(r[k] for r in known if type(r.get(k)) is int) for k in COUNTERS}
    cache_known=all(type(r.get('cached_input_tokens')) is int and 0<=r['cached_input_tokens']<=r['input_tokens'] for r in known)
    values['cached_input_tokens']=values['cached_input_tokens'] if known and cache_known else None
    values['uncached_input_tokens']=values['input_tokens']-values['cached_input_tokens'] if values['cached_input_tokens'] is not None else None
    return dict(runs=len(rows),known_runs=len(known),known_totals=values,partial=len(known)!=len(rows))

def historical_logs(root):
    rows={};unknown=0;limited=False;budget=32*1024*1024;files=0
    paths=sorted((Path(root)/'.scrum/local').glob('worker-*.log'));limited=len(paths)>20
    for path in paths[:20]:
        if path.is_symlink():limited=True;continue
        size=path.stat().st_size
        if size>budget:limited=True;continue
        budget-=size;files+=1;current=None
        with path.open('rb') as stream:
            while True:
                line=stream.readline(1024*1024+1)
                if not line:break
                if len(line)>1024*1024:limited=True;break
                try:event=json.loads(line)
                except (ValueError,UnicodeError):continue
                if not isinstance(event,dict):continue
                if event.get('type')=='thread.started':
                    current=(path.name,event.get('thread_id'))
                    if not isinstance(current[1],str):current=None;unknown+=1
                    elif current not in rows:rows[current]={}
                elif event.get('type')=='turn.completed' and current:
                    usage=event.get('usage')
                    if not isinstance(usage,dict):continue
                    if any(type(usage.get(k)) is not int or not 0<=usage[k]<=10**15 for k in ('input_tokens','output_tokens')):continue
                    cache=usage.get('cached_input_tokens')
                    if cache is not None and (type(cache) is not int or not 0<=cache<=usage['input_tokens']):continue
                    rows[current]={k:usage.get(k) for k in COUNTERS}
    return dict(totals(list(rows.values())+[{} for _ in range(unknown)]),files=files,limited=limited,
                scope='Retained Codex logs, lifetime only. Overlaps recorded executions: never add the two totals. No task, model or time attribution inferred.')

def verification_events(root,start=None,end=None):
    path=Path(root)/'.scrum/local/verification-events.jsonl';rows=[]
    if not path.is_file() or path.is_symlink():return {'events':0,'executed':0,'reused':0,'failed':0,'repeat_failures':0,'limited':False}
    limited=path.stat().st_size>4*1024*1024
    with path.open('rb') as stream:
        if limited:stream.seek(-4*1024*1024,2);stream.readline()
        for line in stream:
            try:r=json.loads(line)
            except ValueError:continue
            if not isinstance(r,dict) or type(r.get('at')) not in (int,float):continue
            if start is not None and not start<=r['at']<=end:continue
            rows.append(r)
    failed=[r for r in rows if r.get('passed') is False and not r.get('reused')]
    signatures=collections.Counter((r.get('signature'),r.get('kind')) for r in failed if r.get('signature'))
    return {'events':len(rows),'executed':sum(not r.get('reused',False) for r in rows),'reused':sum(bool(r.get('reused')) for r in rows),
            'failed':len(failed),'repeat_failures':sum(max(0,n-1) for n in signatures.values()),'limited':limited}

def audit(db,project,root,start=None,end=None,include_logs=False):
    import execution_usage
    execution_usage.ensure(db)
    query='SELECT payload,recorded_at FROM cli_execution_usage WHERE project=?';params=[project]
    if start is not None:query+=' AND recorded_at>=? AND recorded_at<=?';params += [start,end]
    runs=[]
    for row in db.execute(query,params):
        r=json.loads(row['payload'])
        # Claude cache reads/writes are additional to its input, unlike Codex.
        if r.get('client')=='claude':
            vals=[r.get(k) for k in ('input_tokens','cached_input_tokens','cache_write_input_tokens')]
            r['input_tokens']=sum(vals) if all(type(v) is int for v in vals) else None
        tasks=[]
        for task in r.get('tasks',[]):
            t=db.execute('SELECT number,title,category FROM tasks WHERE project=? AND id=?',(project,task)).fetchone()
            if t:tasks.append({'id':task,'number':t['number'],'title':t['title'],'category':t['category']})
        runs.append({**{k:r.get(k) for k in COUNTERS},'run_id':r['run_id'],'model':r['model'],'client':r['client'],'recorded_at':row['recorded_at'],'tasks':tasks})
    result={'schema':1,'generated_at':time.time(),'scope':'Selected interval by local completion-record time; in-flight and earlier uninstrumented executions may be missing.',
            'recorded':totals(runs),'top_runs':sorted(runs,key=lambda r:r.get('input_tokens') or -1,reverse=True)[:15],
            'verification':verification_events(root,start,end),
            'limits':['Verification counts cover instrumented helper calls after installation only; past tests and other commands are unobserved.',
                      'Input totals accumulate across model steps, not unique text or context size. Cache is already included.',
                      'No conversion to account quota or money. No reliable token split between coding, testing and evidence.',
                      'Shared batch executions appear once; their tokens are never divided among tasks. Missing counters are unknown.'],
            'recommendations':[]}
    if start is not None:
        import telemetry
        flow=telemetry.metrics(db,project,start_at=start,end_at=end)
        # A long-lived desktop server may still have an older telemetry module
        # imported during an update. Missing counters are unknown, never zero.
        result['flow']={k:flow.get(k) for k in ('attempted_tasks','metadata_only_tasks','todo_returns_after_work','reopened_done_tasks','completed_tasks','worked_tasks','period_distribution','start_at','end_at')}
    import run_cost
    result['live_executions']=run_cost.current(root)
    import usage_measurement
    measurement=usage_measurement.summary(root)
    if measurement:result['measurement']=measurement
    v=result['verification']
    if v['repeat_failures']:result['recommendations'].append('Repeated failures with identical recorded inputs: inspect environment and reuse diagnostics before another unchanged launch.')
    if any(any(t['category']=='Git recovery' for t in r['tasks']) for r in runs):result['recommendations'].append('Git recovery executions exist: inspect native checkout diagnostics before spending another model run.')
    if include_logs:result['historical_logs']=historical_logs(root)
    return result
