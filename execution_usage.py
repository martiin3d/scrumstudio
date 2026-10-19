"""Reported CLI counters only. Local run totals are never allocated to tasks."""
import json
import time

COUNTERS=('input_tokens','output_tokens','cached_input_tokens','cache_write_input_tokens','reasoning_output_tokens')

def parse(path,offset,client):
    try:
        with open(path,'rb') as stream:
            stream.seek(0,2);end=stream.tell();start=max(offset,end-262144);stream.seek(start)
            if start>offset:stream.readline()
            lines=stream.read().decode('utf-8',errors='replace').splitlines()
    except OSError:return None
    for line in reversed(lines):
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict):continue
        match=event.get('type')==('turn.completed' if client=='codex' else 'result')
        usage=event.get('usage')
        if not match or not isinstance(usage,dict):continue
        mapping={key:key for key in COUNTERS}
        if client=='claude':mapping.update(cached_input_tokens='cache_read_input_tokens',cache_write_input_tokens='cache_creation_input_tokens')
        counts={key:usage.get(field) for key,field in mapping.items()}
        if any(value is not None and (type(value) is not int or not 0<=value<=10**15) for value in counts.values()):return None
        if all(value is None for value in counts.values()):return None
        return counts|{'source':'codex-terminal-turn' if client=='codex' else 'claude-run-summary'}
    return None

def report(path,offset,client,run_id,model,tasks):
    observed=parse(path,offset,client)
    return {'run_id':run_id,'model':model,'tasks':list(dict.fromkeys(tasks)),
            **(observed or dict.fromkeys(COUNTERS)),
            'source':observed['source'] if observed else 'unavailable'}

def ensure(db):
    db.execute('CREATE TABLE IF NOT EXISTS cli_execution_usage(project INTEGER,actor INTEGER,run_id TEXT,payload TEXT,recorded_at REAL,PRIMARY KEY(project,actor,run_id))')

def record(db,project,actor,client,value):
    from scripts.cli_runtime import model_name
    expected={'run_id','model','tasks','source',*COUNTERS}
    if not isinstance(value,dict) or set(value)!=expected:raise ValueError('Invalid CLI usage fields')
    if not isinstance(value['run_id'],str) or not value['run_id'].strip() or len(value['run_id'])>100:raise ValueError('Invalid CLI run ID')
    model_name(value['model'])
    source=value['source']
    if source not in ('unavailable','codex-terminal-turn' if client=='codex' else 'claude-run-summary'):raise ValueError('Invalid CLI usage source')
    for key in COUNTERS:
        n=value[key]
        if n is not None and (type(n) is not int or not 0<=n<=10**15):raise ValueError('Invalid reported CLI token count')
        if source=='unavailable' and n is not None:raise ValueError('Unavailable CLI usage must remain unknown')
    tasks=value['tasks']
    if not isinstance(tasks,list) or len(tasks)>3 or any(type(task) is not int for task in tasks) or len(tasks)!=len(set(tasks)) or any(not db.execute('SELECT 1 FROM tasks WHERE project=? AND id=?',(project,task)).fetchone() for task in tasks):raise ValueError('CLI usage task scope must belong to this project')
    ensure(db)
    payload=json.dumps(value|{'client':client},sort_keys=True,separators=(',',':'))
    old=db.execute('SELECT payload FROM cli_execution_usage WHERE project=? AND actor=? AND run_id=?',(project,actor,value['run_id'])).fetchone()
    if old:
        if old['payload']!=payload:raise ValueError('Conflicting report for the same CLI run')
        return False
    db.execute('INSERT INTO cli_execution_usage VALUES(?,?,?,?,?)',(project,actor,value['run_id'],payload,time.time()))
    return True

def summary(db,project):
    ensure(db)
    reports=[json.loads(row[0]) for row in db.execute('SELECT payload FROM cli_execution_usage WHERE project=?',(project,))]
    def total(key):
        values=[report[key] for report in reports]
        return sum(values) if values and all(value is not None for value in values) else None
    inputs=[]
    for report in reports:
        values=[report['input_tokens']]
        if report['client']=='claude':values += [report['cached_input_tokens'],report['cache_write_input_tokens']]
        inputs.append(sum(values) if all(value is not None for value in values) else None)
    return {'runs':len(reports),'known_runs':sum(report['input_tokens'] is not None and report['output_tokens'] is not None for report in reports),
            'input_tokens':sum(inputs) if inputs and all(value is not None for value in inputs) else None,
            **{key:total(key) for key in COUNTERS if key!='input_tokens'},
            'coverage':'Reported terminal Codex turn or Claude run summary only; missing fields stay unknown. Cache/reasoning are subsets, not extra tokens to add.',
            'attribution':'Each execution counted once; batch totals shared, never divided or duplicated across tasks. Not account quota or money.'}
