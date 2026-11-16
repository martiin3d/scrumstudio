"""On-demand native usage audit. No model, provider requests or raw log export."""
import collections
import hashlib
import json
import math
import re
import time
from pathlib import Path

COUNTERS=('input_tokens','cached_input_tokens','output_tokens')
PATTERN_LIMIT=12
VERIFICATION_ACTIONS={
    'check_failure':('regression_test','Isolate the recurring failure and add a targeted regression check after confirming its cause.'),
    'missing_report':('helper','Fix the report collection helper and verify it produces a fresh report before retrying.'),
    'environment_access':('guide','Document the required environment access and verify it before retrying.'),
    'verification_pass':('reuse','Reuse the passing receipt if the recorded check and its inputs still match.'),
}


def verification_patterns(rows, pattern_id=None):
    groups=collections.defaultdict(set)
    for row in rows:
        signature=row.get('signature')
        if row.get('reused') or not isinstance(signature,str) or not 1<=len(signature)<=128:
            continue
        kind=row.get('kind') if row.get('passed') is False else 'verification_pass' if row.get('passed') is True else None
        if not isinstance(kind,str) or not re.fullmatch(r'[a-z_]{1,40}',kind):
            continue
        groups[(signature,kind)].add(row['at'])
    patterns=[]
    for (signature,kind),stamps in groups.items():
        if len(stamps)<2:
            continue
        fingerprint=hashlib.sha256(signature.encode()).hexdigest()
        identity=hashlib.sha256(f'{kind}:{fingerprint}'.encode()).hexdigest()[:24]
        if pattern_id and identity!=pattern_id:
            continue
        destination,action=VERIFICATION_ACTIONS.get(
            kind,('guide','Record the conditions and diagnosis before repeating this verification.'))
        patterns.append({
            'pattern_id':identity,'kind':'verification_pass' if kind=='verification_pass' else 'verification_failure',
            'failure_kind':kind if kind!='verification_pass' else None,
            'observations':len(stamps),'first_at':min(stamps),'last_at':max(stamps),
            'scope':{'workspace':'project checkout','fingerprint':fingerprint},
            'action':{'destination':destination,'text':action},
        })
    patterns.sort(key=lambda row:(-row['observations'],-row['last_at'],row['pattern_id']))
    return patterns[:PATTERN_LIMIT],len(patterns)>PATTERN_LIMIT

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

def verification_events(root,start=None,end=None,*,pattern_id=None):
    path=Path(root)/'.scrum/local/verification-events.jsonl';rows=[]
    if not path.is_file() or path.is_symlink():return {'events':0,'executed':0,'reused':0,'failed':0,'repeat_failures':0,'repeat_passes':0,'limited':False,'patterns':[],'patterns_limited':False}
    limited=path.stat().st_size>4*1024*1024
    with path.open('rb') as stream:
        if limited:stream.seek(-4*1024*1024,2);stream.readline()
        for line in stream:
            try:r=json.loads(line)
            except ValueError:continue
            if not isinstance(r,dict) or type(r.get('at')) not in (int,float) or not math.isfinite(r['at']):continue
            if start is not None and not start<=r['at']<=end:continue
            rows.append(r)
    failed=[r for r in rows if r.get('passed') is False and not r.get('reused')]
    signatures=collections.Counter((r['signature'],r['kind']) for r in failed
                                   if isinstance(r.get('signature'),str) and r['signature'] and isinstance(r.get('kind'),str))
    passes=collections.Counter(r['signature'] for r in rows
                               if r.get('passed') is True and not r.get('reused') and isinstance(r.get('signature'),str) and r['signature'])
    patterns,patterns_limited=verification_patterns(rows,pattern_id)
    return {'events':len(rows),'executed':sum(not r.get('reused',False) for r in rows),'reused':sum(bool(r.get('reused')) for r in rows),
            'failed':len(failed),'repeat_failures':sum(max(0,n-1) for n in signatures.values()),
            'repeat_passes':sum(n-1 for n in passes.values()),'limited':limited,
            'patterns':patterns,'patterns_limited':patterns_limited}

def audit(db,project,root,start=None,end=None,include_logs=False):
    import execution_usage
    import git_revert_evidence
    from store import project_prefix
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
    import overhead
    by_run={item['run_id']:item for item in runs}
    result['startup_samples']=[
        {**sample,**totals([by_run[sample['run_id']]])['known_totals']}
        if sample['run_id'] in by_run and by_run[sample['run_id']].get('input_tokens') is not None else sample
        for sample in overhead.startup_samples(project,start,end)
    ]
    budgeted=[sample for sample in result['startup_samples'] if 'budget' in sample]
    result['startup_budget']={
        'samples':len(budgeted),
        'with_deferred_context':sum(bool(sample['budget']['deferred']) for sample in budgeted),
        'with_essential_overflow':sum(bool(sample['budget']['overflow_bytes']) for sample in budgeted),
        'scope':'Bounded native startup sample. Byte counts exclude the provider catalog and later tool output.',
    }
    import usage_measurement
    measurement=usage_measurement.summary(root)
    if measurement:result['measurement']=measurement
    v=result['verification']
    reads=overhead.claim_read_repetitions(project,start,end)
    prefix=project_prefix(db.execute('SELECT name FROM projects WHERE id=?',(project,)).fetchone()['name'])
    reverts=git_revert_evidence.patterns(db,project,prefix,root,start,end)
    read_counts={key:value for key,value in reads.items() if key not in ('patterns','patterns_limited')} if reads is not None else None
    result['repetitions']={
        'claim_reads':read_counts,
        'unchanged_verification_passes':v['repeat_passes'],
        'unchanged_verification_failures':v['repeat_failures'],
        'verification_limited':v['limited'],
        'patterns':(reads or {}).get('patterns',[])+v['patterns']+reverts['patterns'],
        'patterns_limited':bool((reads or {}).get('patterns_limited') or v['patterns_limited'] or v['limited'] or reverts['limited']),
        'scope':'Recorded task reads, verification signatures, and exact Git reversals of recorded deliveries in the selected interval. Identical reads match the requested page and returned version. Raw read arguments are not retained. Unrecorded behavior is unknown.',
    }
    result['verification']={key:value for key,value in v.items() if key not in ('patterns','patterns_limited')}
    if reads and reads['sessions_with_repeated_reads']>=2:
        result['recommendations'].append('Repeated task reads occurred in multiple managed sessions. Check whether the initial claim already supplied the requested fields.')
    if reads and reads['identical_versioned_reads']:
        result['recommendations'].append('Identical task pages were fetched for the same version. Review whether the worker can reuse the earlier result.')
    if any(pattern['kind']=='verification_pass' for pattern in v['patterns']):
        result['recommendations'].append('Identical verification inputs passed more than once. Consider receipt reuse where the check and inputs are still valid.')
    if any(pattern['kind']=='verification_failure' for pattern in v['patterns']):
        result['recommendations'].append('Repeated failures with identical recorded inputs: review the scoped patterns before another unchanged launch.')
    if any(any(t['category']=='Git recovery' for t in r['tasks']) for r in runs):result['recommendations'].append('Git recovery executions exist: inspect native checkout diagnostics before spending another model run.')
    if include_logs:result['historical_logs']=historical_logs(root)
    return result
