"""Local work-period metadata from existing control/quota events; no provider calls."""
import math
import sqlite3
import time
import overhead


def ensure(db):
    db.execute('CREATE TABLE IF NOT EXISTS work_periods(id TEXT PRIMARY KEY,kind TEXT,client TEXT,started REAL,ended REAL,observed REAL)')


def capture(project, action, args, result):
    """Instrumentation is best effort and never changes a committed operation."""
    if action not in ('agent_checkpoint','project_control'):return
    try:
        db=overhead.connection(project)
        try:
            with db:
                ensure(db)
                control=result.get('control',{})
                if control.get('changed',0)>0 and not control.get('paused'):
                    key='wakeup:'+str(control['generation']);began=control['changed']
                    db.execute("UPDATE work_periods SET ended=? WHERE kind='wakeup' AND ended IS NULL AND started<?",(began,began))
                    db.execute('INSERT OR IGNORE INTO work_periods VALUES(?,?,?,?,NULL,?)',(key,'wakeup','',began,time.time()))
                if action=='agent_checkpoint':
                    data=overhead.cached_usage(args)
                    reset=data.get('session_resets_at');observed=data.get('observed_at')
                    if (type(reset) in (int,float) and type(observed) in (int,float)
                        and math.isfinite(reset) and math.isfinite(observed)
                        and 0<=time.time()-observed<=300 and reset-18000<=observed<reset):
                        client=args.get('client')
                        if client in ('codex','claude'):
                            key='quota:'+client+':'+str(int(reset))
                            db.execute('INSERT OR IGNORE INTO work_periods VALUES(?,?,?,?,?,?)',(key,'quota',client,reset-18000,reset,observed))
        finally:db.close()
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):pass


def catalog(project):
    db=overhead.connection(project)
    try:
        with db:ensure(db)
        periods=[dict(r) for r in db.execute('SELECT * FROM work_periods ORDER BY started DESC LIMIT 100')]
        runs=[dict(r) for r in db.execute('SELECT s.id,s.actor,s.started,MAX(t.at) AS ended FROM sessions s LEFT JOIN timed_calls t ON t.session=s.id GROUP BY s.id ORDER BY s.started DESC LIMIT 20')]
        return periods+[dict(r,kind='mcp',client='') for r in runs]
    finally:db.close()


def resolve(project,key,at=None):
    at=time.time() if at is None else at
    item=next((r for r in catalog(project) if r['id']==key),None)
    if item is None:raise ValueError('Unknown recorded Metrics period; refresh the period list')
    end=min(item.get('ended') or at,at)
    return dict(item,start_at=item['started'],end_at=max(item['started'],end))
