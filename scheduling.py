"""Deterministic dependency urgency; no inference or priority-field rewrites."""
import collections
import json
import time


def human_only(task):
    return task.get('category','').strip().casefold()=='human review'


def keys(tasks, actor=None, previously_blocked=()):
    pending={t['id']:t for t in tasks if t['status'] not in ('Done','Cancelled')}
    children=collections.defaultdict(set)
    for t in pending.values():
        for dep in t.get('dependencies',[]):
            if dep in pending:children[dep].add(t['id'])
    result={}
    for ident,task in pending.items():
        seen={ident};stack=list(children[ident]);dependents=[];immediate=0
        while stack:
            child=stack.pop()
            if child in seen:continue
            seen.add(child);t=pending[child]
            # A separate external constraint is not resolved by finishing this path.
            if t.get('blocked_reason','').strip() and t.get('blocked_reason_kind')!='dependency':continue
            if human_only(t):continue
            dependents.append(t)
            missing=set(t.get('dependencies',[])).intersection(pending)
            if missing=={ident}:immediate+=1
            stack.extend(children[child]-seen)
        effective=min([task['priority'],*(t['priority'] for t in dependents)])
        live=task['status']=='In Progress' and task.get('owner_id')==actor and task.get('locked',False)
        resumed=task.get('owner_id')==actor and bool(task.get('continuity'))
        reopened=ident in previously_blocked or task['status']=='Blocked'
        result[ident]=(0 if live else 1,effective,-immediate,-len(dependents),0 if reopened else 1,0 if resumed else 1,ident)
    return result


def reconcile(db,project,actor,activity,now,prefix=None):
    """Repair stale dependency-only blocks at native completion/selection boundaries."""
    if not db.in_transaction:
        if not db.execute("SELECT 1 FROM tasks WHERE project=? AND status='Blocked' AND lock_until<=? LIMIT 1",(project,time.time())).fetchone():return []
        db.execute('BEGIN IMMEDIATE')
    rows=db.execute("SELECT * FROM tasks WHERE project=? AND status='Blocked' AND lock_until<=?",(project,time.time())).fetchall()
    if not rows:return []
    done={r['id'] for r in db.execute("SELECT id FROM tasks WHERE project=? AND status='Done'",(project,))}
    changed=[]
    for row in rows:
        deps=json.loads(row['dependencies'])
        if not deps or not set(deps).issubset(done):continue
        if row['blocked_reason'].strip() and row['blocked_reason_kind']!='dependency':continue
        db.execute("UPDATE tasks SET status='TODO',blocked_reason='',blocked_reason_kind='external',updated=? WHERE id=?",(now(),row['id']))
        import telemetry
        telemetry.transition(db,row['id'],'TODO',actor)
        activity(db,row['id'],actor,'comment','Native dependency recheck: all recorded prerequisites are Done; task is ready to retry. Existing evidence and continuity retained.')
        changed.append({'id':row['id'],'status':'TODO','remaining':0,**({'code':f"{prefix}-{row['number']}"} if prefix else {})})
    return changed


def blocked_history(db,project):
    return {r[0] for r in db.execute("SELECT DISTINCT s.task FROM task_states s JOIN tasks t ON t.id=s.task WHERE t.project=? AND s.status='Blocked'",(project,))}
