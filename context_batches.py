"""Bounded sequential context reuse with expiring task reservations."""
import json
import time
import uuid
import coordination as coord
import context_tools
import model_routing

def ensure(db):
    db.execute("CREATE TABLE IF NOT EXISTS context_batches(project INTEGER,actor INTEGER,client TEXT,model TEXT,members TEXT,position INTEGER NOT NULL DEFAULT 0,done INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(project,actor))")
    if 'group_id' not in {r['name'] for r in db.execute('PRAGMA table_info(context_batches)')}:
        db.execute("ALTER TABLE context_batches ADD COLUMN group_id TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE TABLE IF NOT EXISTS context_batch_history(id TEXT PRIMARY KEY,project INTEGER,actor INTEGER,created REAL,members TEXT,model TEXT)')

def live(db,project):
    # The existing supervisor heartbeat renews the offer; no extra polling.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='context_batches'").fetchone():return []
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='worker_launch_offers'").fetchone():return []
    if db.execute('SELECT 1 FROM project_control WHERE project=? AND paused=1',(project,)).fetchone():return []
    return db.execute('SELECT b.*,o.expires,u.name FROM context_batches b JOIN worker_launch_offers o ON o.project=b.project AND o.actor=b.actor JOIN users u ON u.id=b.actor WHERE b.project=? AND b.done=0 AND o.expires>? AND u.active=1',(project,time.time())).fetchall()

def reservation(db,project,task):
    for row in live(db,project):
        members=json.loads(row['members'])
        if task in members[row['position']:]:
            return {'actor':row['actor'],'name':row['name'],'current':members[row['position']],'expires':row['expires'],'members':members}

def check_claim(db,project,task,actor):
    held=reservation(db,project,task)
    if held and (held['actor']!=actor or held['current']!=task):
        raise ValueError('Task reserved by context batch; finish its current task or release the batch reservation first')

def detail(db,project,task,prefix):
    held=None
    for row in live(db,project):
        members=json.loads(row['members'])
        if task in members:
            held={'actor':row['actor'],'name':row['name'],'current':members[row['position']],'expires':row['expires'],'members':members}
            break
    if not held:return None
    items=[]
    for ident in held.pop('members'):
        row=db.execute('SELECT id,number,title,status FROM tasks WHERE project=? AND id=?',(project,ident)).fetchone()
        if row:items.append({'id':row['id'],'code':f"{prefix}-{row['number']}",'title':row['title'],'status':row['status']})
    return {**held,'tasks':items}

def compatible(root,task):
    return (task['scope_size']!='Large' and 0<len(task['files'])<=5 and
            task['priority']==root['priority'] and bool(set(root['files']).intersection(task['files'])))

def ready(db,project,task,allowed=()):
    if task['blocked_reason'].strip():return False
    return all(dep in allowed or db.execute("SELECT 1 FROM tasks WHERE id=? AND project=? AND status='Done'",(dep,project)).fetchone() for dep in task['dependencies'])

def plan(db,project,actor,root,tasks,selection,folder,size=3):
    ensure(db)
    members=[root];used={root['id']};total=len(json.dumps(context_tools.agent_task(root)).encode())
    isolated=db.execute('SELECT git_mode FROM projects WHERE id=?',(project,)).fetchone()[0]=='Worktrees'
    profile=model_routing.profile_model(folder,actor)
    available=model_routing.catalog() if selection['client']=='codex' else None
    if root['scope_size']!='Large' and 0<len(root['files'])<=5:
        for task in tasks:
            if len(members)>=size:break
            if isolated and (not root.get('context_group') or task.get('context_group')!=root['context_group']):continue
            if task['id'] in used or task['locked'] or task['status'] not in ('TODO','Blocked') or not compatible(root,task) or not ready(db,project,task,used):continue
            held=reservation(db,project,task['id'])
            if held and held['actor']!=actor:continue
            if db.execute('SELECT 1 FROM worker_launch_offers WHERE project=? AND task=? AND actor!=? AND expires>?',(project,task['id'],actor,time.time())).fetchone():continue
            try:
                if coord.conflicts(db,coord.canonical_paths(folder,task['files']),task['id'],actor):continue
                chosen=model_routing.select(task,selection['client'],profile,available)
            except (OSError,ValueError):continue
            if chosen['model']!=selection['model']:continue
            cost=len(json.dumps(context_tools.agent_task(task)).encode())
            if cost>4000 or total+cost>10000:continue
            members.append(task);used.add(task['id']);total+=cost
    ids=[task['id'] for task in members]
    previous=db.execute('SELECT * FROM context_batches WHERE project=? AND actor=?',(project,actor)).fetchone()
    group_id=''
    if len(ids)>1:
        group_id=previous['group_id'] if previous and not previous['done'] and previous['model']==selection['model'] and previous['client']==selection['client'] and json.loads(previous['members'])==ids else ''
        if not group_id:
            group_id=uuid.uuid4().hex
            db.execute('INSERT INTO context_batch_history VALUES(?,?,?,?,?,?)',(group_id,project,actor,time.time(),json.dumps(ids),selection['model']))
    db.execute('INSERT INTO context_batches(project,actor,client,model,members,group_id) VALUES(?,?,?,?,?,?) ON CONFLICT(project,actor) DO UPDATE SET client=excluded.client,model=excluded.model,members=excluded.members,group_id=excluded.group_id,position=0,done=0',(project,actor,selection['client'],selection['model'],json.dumps(ids),group_id))
    return [{key:task[key] for key in ('id','code','title')} for task in members]

def browser_groups(project,task_ids):
    """Human-only, checkout-local batch history; never injected into AI task reads."""
    import storage
    from store import connect
    from contextlib import closing
    _,folder=storage.entry(project)
    main,locks=storage.local_paths(folder)
    with closing(connect(main,locks)) as db:
        local=db.execute('SELECT id FROM projects').fetchone()[0]
        wanted=set(task_ids);seen=set();groups={}
        active={}
        for row in live(db,local):
            members=json.loads(row['members'])
            if len(members)<2:continue
            key=dict(row).get('group_id') or 'live-'+str(row['actor'])+'-'+str(members[0])
            group={'id':key,'actor':row['actor'],'name':row['name'],'created':0,'tasks':members,'active':True,'reserved_tasks':members[row['position']:]}
            active[key]=group
            groups[key]=group;seen.update(wanted.intersection(members))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='context_batch_history'").fetchone():
            rows=db.execute('SELECT h.*,u.name,j.value AS task FROM context_batch_history h JOIN json_each(h.members) j LEFT JOIN users u ON u.id=h.actor WHERE h.project=? ORDER BY h.created DESC,h.id',(local,))
            for row in rows:
                key=row['id']
                if key in active:
                    groups[key]['created']=row['created'];continue
                if row['task'] not in wanted or row['task'] in seen:continue
                seen.add(row['task'])
                if key not in groups:groups[key]={'id':key,'actor':row['actor'],'name':row['name'] or 'Former agent','created':row['created'],'tasks':[],'active':False,'reserved_tasks':[]}
                groups[key]['tasks'].append(row['task'])
        return list(groups.values())

def advance(db,project,actor,completed,policy,prefix):
    from store import task_dict
    import runtime
    ensure(db)
    row=db.execute('SELECT * FROM context_batches WHERE project=? AND actor=?',(project,actor)).fetchone()
    if not row:raise ValueError('Batch unavailable; completion is saved, close safely')
    members=json.loads(row['members']);position=row['position']
    if completed not in members or members.index(completed)>position:raise ValueError('Task is outside current batch position')
    if not db.execute("SELECT 1 FROM tasks WHERE project=? AND id=? AND status='Done'",(project,completed)).fetchone():raise ValueError('Finish the current task before advancing')
    stop=runtime.reason(db,project,actor)
    if stop:return {'batch_done':True,'should_stop':True,'reason':stop}
    if row['done']:return {'batch_done':True}
    if members.index(completed)==position:position+=1
    root=task_dict(db.execute('SELECT * FROM tasks WHERE id=?',(members[0],)).fetchone(),prefix)
    profile=model_routing.profile_model(policy['workspace_root'],actor)
    available=model_routing.catalog() if row['client']=='codex' else None
    while position<len(members):
        task=task_dict(db.execute('SELECT * FROM tasks WHERE id=? AND project=?',(members[position],project)).fetchone(),prefix)
        foreign=task['locked'] and task['owner_id']!=actor
        offered=db.execute('SELECT 1 FROM worker_launch_offers WHERE project=? AND task=? AND actor!=? AND expires>?',(project,task['id'],actor,time.time())).fetchone()
        if foreign or offered or task['status'] not in ('TODO','Blocked','In Progress') or not ready(db,project,task):position+=1;continue
        if task['status']=='In Progress' and not (task['locked'] and task['owner_id']==actor):position+=1;continue
        if not compatible(root,task):break
        try:
            chosen=model_routing.select(task,row['client'],profile,available)
            paths=coord.canonical_paths(policy['workspace_root'],task['files'])
        except (ValueError,OSError):break
        if chosen['model']!=row['model']:break
        if coord.conflicts(db,paths,task['id'],actor):position+=1;continue
        db.execute('UPDATE context_batches SET position=? WHERE project=? AND actor=?',(position,project,actor))
        db.execute('UPDATE worker_launch_offers SET task=?,expires=? WHERE project=? AND actor=?',(task['id'],time.time()+600,project,actor))
        db.execute('UPDATE tasks SET model_selection=? WHERE id=?',(json.dumps(chosen),task['id']))
        task['model_selection']=chosen
        return {'task':context_tools.selection_header(task),'batch_remaining':len(members)-position,'ownership_granted':False}
    db.execute('UPDATE context_batches SET position=?,done=1 WHERE project=? AND actor=?',(len(members),project,actor))
    return {'batch_done':True}

def release(db,project,actor):
    ensure(db)
    row=db.execute('SELECT * FROM context_batches WHERE project=? AND actor=?',(project,actor)).fetchone()
    db.execute('DELETE FROM context_batches WHERE project=? AND actor=?',(project,actor))
    if not row:return {}
    members=json.loads(row['members'])
    result={'batch_complete':bool(row['done']),'batch_tasks':members}
    if row['position']<len(members):
        current=members[row['position']]
        task=db.execute('SELECT model_selection FROM tasks WHERE project=? AND id=?',(project,current)).fetchone()
        result.update(batch_current=current,batch_selection=json.loads(task['model_selection']) if task else {})
    return result
