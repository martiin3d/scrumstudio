"""Calendar-time state tracking and compact, on-demand project metrics."""
import time
from datetime import datetime, timedelta, timezone

STATUSES = ['TODO','In Progress','Blocked','In Review','Done','Cancelled']


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS task_states(
        id INTEGER PRIMARY KEY AUTOINCREMENT, task INTEGER NOT NULL REFERENCES tasks(id),
        status TEXT NOT NULL, entered REAL NOT NULL, exited REAL, actor INTEGER REFERENCES users(id),
        source TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS task_state_history ON task_states(task,id)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS task_current_state ON task_states(task) WHERE exited IS NULL')
    db.execute('''INSERT INTO task_states(task,status,entered,source)
        SELECT id,status,?,'baseline' FROM tasks WHERE NOT EXISTS(SELECT 1 FROM task_states s WHERE s.task=tasks.id)''',(time.time(),))
    if 'cancel_reason' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
        db.execute("ALTER TABLE tasks ADD COLUMN cancel_reason TEXT NOT NULL DEFAULT ''")


def transition(db, task, status, actor=None, at=None, source='transition'):
    at=time.time() if at is None else at
    previous=db.execute('SELECT * FROM task_states WHERE task=? AND exited IS NULL',(task,)).fetchone()
    if previous and previous['status']==status:
        return None
    result=None
    if previous:
        at=max(at,previous['entered'])
        db.execute('UPDATE task_states SET exited=? WHERE id=?',(at,previous['id']))
        result={'from':previous['status'],'seconds':round(at-previous['entered'],3)}
    db.execute('INSERT INTO task_states(task,status,entered,actor,source) VALUES(?,?,?,?,?)',(task,status,at,actor,source))
    return result


def timing(db, task, history=False, before=None, limit=20, at=None):
    at=time.time() if at is None else at
    rows=db.execute('SELECT * FROM task_states WHERE task=? ORDER BY id',(task,)).fetchall()
    seconds={s:0.0 for s in STATUSES}
    for row in rows:
        seconds[row['status']]+=max(0,min(row['exited'] if row['exited'] is not None else at,at)-row['entered'])
    current=rows[-1] if rows else None
    result={'seconds_by_state':{k:round(v,3) for k,v in seconds.items()},'tracked_since':rows[0]['entered'] if rows else None,'as_of':at,
            'partial_history':bool(rows and rows[0]['source']=='baseline'),'current_state':current['status'] if current else None,
            'current_state_seconds':round(max(0,at-current['entered']),3) if current else 0,
            'basis':'Calendar seconds, including waiting; terminal residence also accumulates. Legacy time before tracking is unknown.'}
    if history:
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError('limit must be 1..50')
        selected=[r for r in reversed(rows) if before is None or r['id']<before]
        result['has_more']=len(selected)>limit
        selected=selected[:limit]
        result['intervals']=[dict(r,seconds=round(max(0,(r['exited'] if r['exited'] is not None else at)-r['entered']),3)) for r in selected]
        result['oldest']=selected[-1]['id'] if selected else None
    return result


def metrics(db, project, days=30, include_series=False, at=None, start_at=None, end_at=None):
    if type(days) is not int or not 1<=days<=365 or type(include_series) is not bool:
        raise ValueError('days must be 1..365; include_series must be boolean')
    at=time.time() if at is None else at
    # Display complete UTC date buckets, including today.
    today=datetime.fromtimestamp(at,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    start=(today-timedelta(days=days-1)).timestamp()
    if start_at is not None or end_at is not None:
        import math
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start_at,end_at)) or not 0<=start_at<=end_at or end_at-start_at>366*86400:
            raise ValueError('Use valid start_at/end_at timestamps spanning at most 366 days')
        start=start_at;at=min(at,end_at)
        today=datetime.fromtimestamp(at,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        days=max(1,(today-datetime.fromtimestamp(start,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)).days+1)
    tasks=db.execute('SELECT id,status,owner_id,lock_until FROM tasks WHERE project=?',(project,)).fetchall()
    rows=db.execute('SELECT s.* FROM task_states s JOIN tasks t ON t.id=s.task WHERE t.project=? ORDER BY s.task,s.id',(project,)).fetchall()
    distribution={s:0 for s in STATUSES}
    for task in tasks:
        distribution[task['status']]+=1
    state_seconds={s:0.0 for s in STATUSES}
    state_tasks={s:set() for s in STATUSES}
    by_task={}
    done_events=[]
    cancelled_events=[]
    for row in rows:
        by_task.setdefault(row['task'],[]).append(row)
        elapsed=max(0,min(row['exited'] if row['exited'] is not None else at,at)-max(row['entered'],start))
        if elapsed>0:
            state_seconds[row['status']]+=elapsed
            state_tasks[row['status']].add(row['task'])
        if row['source']!='baseline' and start<=row['entered']<=at:
            if row['status']=='Done':
                done_events.append(row)
            elif row['status']=='Cancelled':
                cancelled_events.append(row)
    lead,cycle=[],[]
    legacy=0
    for intervals in by_task.values():
        if intervals[0]['source']=='baseline':
            legacy+=1
            continue
        finished=[r for r in intervals if r['status']=='Done' and start<=r['entered']<=at]
        if not finished:
            continue
        last=finished[-1]['entered']
        lead.append(max(0,last-intervals[0]['entered']))
        began=next((r['entered'] for r in intervals if r['status']=='In Progress' and r['entered']<=last),None)
        if began is not None:
            cycle.append(max(0,last-began))
    nonterminal=sum(state_seconds[s] for s in STATUSES if s not in ['Done','Cancelled'])
    result={'days':days,'start_at':start,'end_at':at,'from_utc':datetime.fromtimestamp(start,timezone.utc).isoformat(),'as_of_utc':datetime.fromtimestamp(at,timezone.utc).isoformat(),
            'total_tasks':len(tasks),'distribution':distribution,
            'open_tasks':sum(distribution[s] for s in STATUSES if s not in ['Done','Cancelled']),
            'done_transitions':len(done_events),'completed_tasks':len({r['task'] for r in done_events}),
            'cancelled_transitions':len(cancelled_events),
            'average_lead_seconds':round(sum(lead)/len(lead),3) if lead else None,'lead_samples':len(lead),
            'average_cycle_seconds':round(sum(cycle)/len(cycle),3) if cycle else None,'cycle_samples':len(cycle),
            'state_seconds_in_window':{s:round(v,3) for s,v in state_seconds.items()},
            'average_observed_state_seconds':{s:round(state_seconds[s]/len(state_tasks[s]),3) if state_tasks[s] else None for s in STATUSES},
            'state_samples':{s:len(state_tasks[s]) for s in STATUSES},
            'blocked_time_percent':round(100*state_seconds['Blocked']/nonterminal,2) if nonterminal else 0,
            'legacy_tasks':legacy,'basis':'Calendar time. Distribution is current; flow and observed state time use selected UTC dates. Legacy histories are partial and excluded from lead/cycle averages. Done transitions count re-completions.'}
    # Work means recorded active-state overlap or an authored activity event,
    # never merely a task waiting in TODO/Blocked/Done during the window.
    worked={r['task'] for r in rows if r['status']=='In Progress' and r['entered']<=at and (r['exited'] is None or r['exited']>=start)}
    worked.update(r[0] for r in db.execute('SELECT DISTINCT a.task FROM task_activity a JOIN tasks t ON t.id=a.task WHERE t.project=? AND julianday(a.created)>=julianday(?) AND julianday(a.created)<=julianday(?)',(project,result['from_utc'],result['as_of_utc'])))
    result['worked_task_ids']=sorted(worked)
    period_distribution={s:0 for s in STATUSES}
    for task in worked:
        visits=[r for r in by_task.get(task,[]) if r['entered']<=at]
        if visits:period_distribution[visits[-1]['status']]+=1
    result['period_distribution']=period_distribution
    result['worked_tasks']=len(worked)
    result['interval_filtered']=start_at is not None

    if include_series:
        buckets={(today-timedelta(days=n)).date().isoformat():{'done':0,'cancelled':0} for n in reversed(range(days))}
        for status,events in [('done',done_events),('cancelled',cancelled_events)]:
            for row in events:
                key=datetime.fromtimestamp(row['entered'],timezone.utc).date().isoformat()
                buckets[key][status]+=1
        result['daily']=[dict(date=key,**values) for key,values in buckets.items()]
    return result
