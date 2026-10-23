"""Local aggregate MCP transport measurements; never store request contents."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


def path(project):
    import storage
    if 'SCRUM_DB' in os.environ and 'SCRUM_REGISTRY' not in os.environ:
        return Path(os.environ['SCRUM_DB']).with_suffix(f'.{project}.overhead.db')
    return storage.entry(project)[1] / '.scrum' / 'local' / 'overhead.db'


def connection(project):
    db = sqlite3.connect(path(project), timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS calls(tool TEXT PRIMARY KEY,calls INTEGER,errors INTEGER,request_bytes INTEGER,response_bytes INTEGER,elapsed_ms REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,actor INTEGER,started REAL,start_mode TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS scoped(session TEXT,task INTEGER,tool TEXT,calls INTEGER,errors INTEGER,request_bytes INTEGER,response_bytes INTEGER,elapsed_ms REAL,PRIMARY KEY(session,task,tool))')
    return db


def start_session(project,actor,mode,existing=None):
    db=connection(project)
    try:
        with db:
            if existing:
                row=db.execute('SELECT actor FROM sessions WHERE id=?',(existing,)).fetchone()
                if not row or row['actor']!=actor:raise ValueError('Unknown measurement session for this actor/project')
                return existing
            key=uuid.uuid4().hex
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(key,actor,time.time(),mode))
            return key
    finally:
        db.close()


def record(project, tool, request_bytes, response_bytes, elapsed_ms, failed, session=None, task=None, actor=None):
    if project is None:
        return
    try:
        db = connection(project)
        try:
            with db:
                db.execute('INSERT INTO calls VALUES(?,1,?,?,?,?) ON CONFLICT(tool) DO UPDATE SET calls=calls+1,errors=errors+excluded.errors,request_bytes=request_bytes+excluded.request_bytes,response_bytes=response_bytes+excluded.response_bytes,elapsed_ms=elapsed_ms+excluded.elapsed_ms', (tool, int(failed), request_bytes, response_bytes, elapsed_ms))
                row=db.execute('SELECT actor FROM sessions WHERE id=?',(session,)).fetchone() if isinstance(session,str) else None
                session=session if row and (actor is None or row['actor']==actor) else ''
                task=task if type(task) is int and task>0 else 0
                db.execute('INSERT INTO scoped VALUES(?,?,?,1,?,?,?,?) ON CONFLICT(session,task,tool) DO UPDATE SET calls=calls+1,errors=errors+excluded.errors,request_bytes=request_bytes+excluded.request_bytes,response_bytes=response_bytes+excluded.response_bytes,elapsed_ms=elapsed_ms+excluded.elapsed_ms',(session,task,tool,int(failed),request_bytes,response_bytes,elapsed_ms))
        finally:
            db.close()
    except (OSError, ValueError, sqlite3.Error):
        pass # Measurement failure must never change a committed operation.


def metrics(project, filter_session=None, task=None):
    db = connection(project)
    try:
        rows = [dict(r) for r in db.execute('SELECT * FROM calls ORDER BY calls DESC,tool')]
        sums=','.join(f'COALESCE(SUM(c.{k}),0) AS {k}' for k in ('calls','errors','request_bytes','response_bytes','elapsed_ms'))
        sessions=[dict(r) for r in db.execute('SELECT s.id,s.actor,s.started,s.start_mode,'+sums+' FROM sessions s LEFT JOIN scoped c ON c.session=s.id GROUP BY s.id ORDER BY s.started DESC LIMIT 20')]
        tasks=[dict(r) for r in db.execute('SELECT c.task,'+sums+' FROM scoped c WHERE c.task>0 GROUP BY c.task ORDER BY calls DESC,c.task LIMIT 50')]
        if filter_session is not None or task is not None:
            where=[];values=[]
            if filter_session is not None:
                if not isinstance(filter_session,str) or len(filter_session)>64:raise ValueError('Invalid measurement session')
                where.append('c.session=?');values.append(filter_session)
            if task is not None:
                if type(task) is not int or task<1:raise ValueError('Invalid task id')
                where.append('c.task=?');values.append(task)
            rows=[dict(r) for r in db.execute('SELECT c.tool,'+sums+' FROM scoped c WHERE '+' AND '.join(where)+' GROUP BY c.tool ORDER BY calls DESC',values)]
            tasks=[dict(r) for r in db.execute('SELECT c.task,'+sums+' FROM scoped c WHERE c.task>0 AND '+' AND '.join(where)+' GROUP BY c.task ORDER BY calls DESC,c.task LIMIT 50',values)]
    finally:
        db.close()
    totals = {key: sum(r[key] for r in rows) for key in ('calls','errors','request_bytes','response_bytes','elapsed_ms')}
    return {'scope': 'Local MCP calls since instrumentation; excludes web calls and model execution', 'totals': totals, 'tools': rows,'sessions':sessions,'tasks':tasks,'session_limit':20,'task_limit':50, 'tokens': None, 'note': 'Bytes are UTF-8 JSON, not tokens. Server time excludes model time. Sessions use explicit measurement_session; untagged calls remain unattributed. start_mode is initial eligibility, not a claim that the entire session used that mode. Compare equivalent work before claiming savings.'}


def usage_path(project, actor, client):
    import storage
    if type(actor) is not int or client not in ('codex', 'claude'):
        raise ValueError('Valid actor and client required')
    return storage.entry(project)[1] / '.scrum' / 'local' / f'usage-{actor}-{client}.json'


def cached_usage(a):
    if any(k in a for k in ('session_used_percent','weekly_used_percent')):
        return a
    try:
        file = usage_path(a['project'], a['actor'], a['client'])
        if a['client'] == 'claude' and not file.exists():
            # The documented Claude status-line feed is shared by the checkout.
            file = file.with_name('claude-usage.json')
        if file.stat().st_size > 10000:
            return a
        data = json.loads(file.read_text(encoding='utf-8'))
        allowed = ('observed_at','session_used_percent','weekly_used_percent','session_resets_at','weekly_resets_at')
        # Timestamp is mandatory: reading an old file must not refresh it.
        if 'observed_at' not in data:
            return a
        return a | {k: data[k] for k in allowed if k in data}
    except (OSError,ValueError,KeyError,TypeError):
        return a
