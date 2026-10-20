"""Local cooperative agent control. Never exported, never fabricates usage."""
import math
import time
import json
import telemetry

def migrate(db, separate=False):
    ns='reservations.' if separate else ''
    db.execute(f'''CREATE TABLE IF NOT EXISTS {ns}agent_sessions(
        project INTEGER NOT NULL, actor INTEGER NOT NULL, client TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'paused', heartbeat REAL NOT NULL,
        session_used_percent REAL, weekly_used_percent REAL, usage_at REAL NOT NULL DEFAULT 0,
        session_resets_at REAL, weekly_resets_at REAL, reason TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(project,actor))''')
    db.execute(f'''CREATE TABLE IF NOT EXISTS {ns}project_control(
        project INTEGER PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0,
        generation INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
        changed REAL NOT NULL, actor INTEGER)''')
    columns={r['name'] for r in db.execute(f'PRAGMA {ns}table_info(agent_sessions)')}
    if 'bridge_heartbeat' not in columns:
        db.execute(f'ALTER TABLE {ns}agent_sessions ADD COLUMN bridge_heartbeat REAL NOT NULL DEFAULT 0')
    for name,spec in [('run_exit_code','INTEGER'),('run_error',"TEXT NOT NULL DEFAULT ''")]:
        if name not in columns:db.execute(f'ALTER TABLE {ns}agent_sessions ADD COLUMN {name} {spec}')
    for name,spec in [('resume_reset','REAL'),('resume_generation','INTEGER'),('auto_resume_status',"TEXT NOT NULL DEFAULT 'disabled'")]:
        if name not in columns:db.execute(f'ALTER TABLE {ns}agent_sessions ADD COLUMN {name} {spec}')


def quota_resume(db,project,actor,mode,why,old,running=False):
    """Persist pause provenance and atomically consume one supervised reset."""
    policy=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
    session=db.execute('SELECT * FROM agent_sessions WHERE project=? AND actor=?',(project,actor)).fetchone()
    c=control(db,project);at=time.time()
    marker=session['resume_reset']
    state=session['auto_resume_status']
    consumed=False
    if not policy['auto_resume_session_quota']:
        marker=None;state='disabled'
    elif c['paused'] or (marker is not None and session['resume_generation']!=c['generation']):
        marker=None;state='cancelled by human control'
    elif mode in ('pause','offline') and not why.startswith('5h usage '):
        marker=None;state='cancelled by manual pause or session close'
    elif state.startswith('cancelled') and mode=='observe':
        marker=None # Late supervisor observations cannot undo an explicit pause.
    elif mode=='check' and not why:
        state='active session; no quota pause'
    elif why.startswith('5h usage ') and (mode=='check' or mode=='observe' and running or mode in ('pause','offline') and old and old['resume_reset'] is not None):
        reset=session['session_resets_at']
        if reset is not None and reset>at and session['usage_at']>=at-max(30,policy['usage_check_seconds']*2):
            marker=reset;state='waiting for fresh session reset; requires connected CLI supervisor'
            db.execute('UPDATE agent_sessions SET resume_generation=? WHERE project=? AND actor=?',(c['generation'],project,actor))
    if marker is not None and mode=='observe' and not running and not why:
        fresh=at-max(30,policy['usage_check_seconds']*2)<=session['usage_at']<=at+5
        known=all(session[k] is not None for k in ('session_used_percent','weekly_used_percent','session_resets_at','weekly_resets_at'))
        eligible=(fresh and known and old and old['client']==session['client'] and at>=marker
                  and session['usage_at']>=marker and session['session_resets_at']>marker
                  and session['session_resets_at']>at and session['weekly_resets_at']>at
                  and session['session_used_percent']<policy['session_stop_percent']
                  and session['weekly_used_percent']<policy['weekly_stop_percent'])
        if eligible:
            marker=None;state='session reset verified; automatic resume requested';consumed=True
    db.execute('UPDATE agent_sessions SET resume_reset=?,auto_resume_status=? WHERE project=? AND actor=?',(marker,state,project,actor))
    return {'requested':consumed,'status':state}

def control(db, project):
    row=db.execute('SELECT * FROM project_control WHERE project=?',(project,)).fetchone()
    return dict(row) if row else {'project':project,'paused':0,'generation':0,'reason':'','changed':0,'actor':None}

def quota_reason(policy, session, at=None):
    at=time.time() if at is None else at
    if not session or at-session['usage_at']>max(30,policy['usage_check_seconds']*2):
        return 'Usage unknown or stale; report both provider windows before work' if policy['usage_guard'] else ''
    for field,reset,limit,label in [('session_used_percent','session_resets_at','session_stop_percent','5h'),('weekly_used_percent','weekly_resets_at','weekly_stop_percent','weekly')]:
        value=session[field]
        if value is None or (session[reset] is not None and session[reset]<=at):
            if policy['usage_guard']:
                return 'Usage unknown or reset; refresh provider telemetry before work'
            continue
        if value>=policy[limit]:
            return f'{label} usage {value:g}% reached stop threshold {policy[limit]}%'
    return ''

def reason(db, project, actor):
    c=control(db,project)
    user=db.execute('SELECT kind FROM users WHERE id=?',(actor,)).fetchone()
    if user and user['kind']=='human':
        return ''
    if c['paused']:
        return c['reason'] or 'Project stopped by human'
    policy=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
    session=db.execute('SELECT * FROM agent_sessions WHERE project=? AND actor=?',(project,actor)).fetchone()
    isolated=any(r['name']=='reservations' for r in db.execute('PRAGMA database_list'))
    if not session and not isolated:
        return '' # Legacy single-file clients retain compatibility.
    return quota_reason(policy,session)

def gate(db, project, actor):
    stop=reason(db,project,actor)
    if stop:
        raise ValueError('Stop work: '+stop+'. Use agent_checkpoint to save handoff and release own claims.')

def wakeup_readiness(policy,sessions,project_control,at):
    """Observed recipients, not a claim that a signal launched a provider."""
    connected=[s for s in sessions if s['bridge_connected']]
    running=[s for s in connected if s['status'] in ('working','stopping')]
    held=[]
    failed=[s for s in connected if s.get('run_exit_code') not in (None,0) and s['status'] in ('waiting','offline')]
    ready=0
    for session in connected:
        why=quota_reason(policy,session,at)
        if why:held.append({'actor':session['actor'],'name':session['name'],'reason':why})
        elif session not in running and session not in failed:ready+=1
    if project_control['paused']:
        status='stopped'
        message='Project stopped. Wakeup clears the project Stop signal; each agent still checks its quota.'
        next_step='Click Wakeup to request continuation, or open Agents to inspect individual workers.'
    elif not connected:
        status='no_connected_bridge'
        message='No agent connection is running. Wakeup can start the saved agent set.'
        next_step='Wakeup restores enabled profiles, or creates a default Codex agent when no profiles exist. Individually stopped profiles stay stopped. Open Agents to choose or authorize a provider. Desktop chats are not reopened.'
    elif ready:
        status='ready'
        message=f'{ready} connected agent(s) ready for a Wakeup request; {len(running)} already running and {len(held)} held by usage checks.'
        next_step='Click Wakeup, then check Agents or Chat for the launch result. Quota, available work and file reservations are checked before execution.'
    elif failed:
        status='failed'
        message=f'{len(failed)} agent run(s) failed before completing work.'
        next_step=' '.join(s['name']+': '+(s.get('run_error') or f"CLI exit {s['run_exit_code']}") for s in failed)+' Resolve the runtime/model error, then send Wakeup again.'
    elif running:
        status='already_running'
        message=f'{len(running)} connected agent(s) already running. Wakeup does not start another copy.'
        next_step='Open Agents or Chat to inspect current work. Any held agents still require fresh quota below the configured limits.'
    else:
        status='held'
        message=f'Wakeup cannot start work yet: {len(held)} connected agent(s) held by quota checks.'
        next_step=' '.join(f"{item['name']}: {item['reason']}." for item in held)+' Refresh provider telemetry or wait for quota availability, then send a new Wakeup.'
    return {'status':status,'connected_bridges':len(connected),'ready_bridges':ready,'running_bridges':len(running),
            'held_bridges':len(held),'held':held,'failed_bridges':len(failed),'can_receive_signal':bool(connected),'workers_started':False,
            'message':message,'next_step':next_step}

def summary(db, project):
    at=time.time()
    policy=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
    c=control(db,project)
    sessions=[dict(r) for r in db.execute('SELECT s.*,u.name FROM agent_sessions s JOIN users u ON u.id=s.actor WHERE s.project=? AND u.active=1 ORDER BY s.actor',(project,))]
    active=set()
    for s in sessions:
        s['usage_fresh']=at-s['usage_at']<=max(30,policy['usage_check_seconds']*2)
        s['bridge_connected']=s['status']!='offline' and s['bridge_heartbeat']>0 and 0<=at-s['bridge_heartbeat']<(30 if s['status'] in ('working','stopping') else 90)
        s['stop_pending']=bool(c['paused'] or quota_reason(policy,s,at))
        s['active']=s['status'] in ['working','stopping'] and at-s['heartbeat']<=max(90,policy['usage_check_seconds']*2)
        if s['active']:
            active.add(s['actor'])
    tasks=[r['id'] for r in db.execute("SELECT t.id,t.owner_id,u.kind FROM tasks t JOIN users u ON u.id=t.owner_id WHERE t.project=? AND t.status='In Progress' AND t.lock_until>?",(project,at)) if r['kind']=='human' or r['owner_id'] in active]
    import coordination
    return {'workflow':coordination.workflow_mode(db,project),'control':c,'active_agents':len(active),'active_tasks':len(tasks),'active_task_ids':tasks,'agents':sessions,'wakeup':wakeup_readiness(policy,sessions,c,at),'basis':'Recent cooperative heartbeat plus valid claim; not OS process detection'}

def handoff(db, project, actor, why, text, now, activity):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='worker_launch_offers'").fetchone():
        db.execute('DELETE FROM worker_launch_offers WHERE project=? AND actor=?',(project,actor))
    tasks=db.execute("SELECT * FROM tasks WHERE project=? AND owner_id=? AND lock_until>?",(project,actor,time.time())).fetchall()
    stamp=now()
    root=db.execute('SELECT workspace_root FROM projects WHERE id=?',(project,)).fetchone()['workspace_root'] if tasks else ''
    for task in tasks:
        status='TODO' if task['status']=='In Progress' else task['status']
        db.execute('UPDATE tasks SET status=?,lock_token=NULL,lock_until=0,updated=? WHERE id=?',(status,stamp,task['id']))
        db.execute('DELETE FROM file_locks WHERE task=?',(task['id'],))
        telemetry.transition(db,task['id'],status,actor)
        last=db.execute("SELECT text FROM task_activity WHERE task=? AND actor=? AND kind='work' ORDER BY id DESC LIMIT 1",(task['id'],actor)).fetchone()
        # The task already carries checklist, acceptance, files and evidence.
        # Keep one bounded continuation, never recursively embed prior handoffs.
        parts=[f'Paused: {why}.']
        if text:parts.append(text[:800])
        if last and last['text'] not in text:parts.append('Last recorded progress: '+last['text'][:600])
        if not text and task['continuity']:
            prior='\n'.join(line for line in task['continuity'].splitlines() if not line.startswith(('Paused:','Next:')))
            if prior and not any(prior in part for part in parts):parts.append(prior[:800])
        import git_changes
        # Factual edit list from local code: the next session knows what to inspect.
        edits=git_changes.summary(root,json.loads(task['files'] or '[]'))
        if edits:parts.append(edits)
        if len(parts)==1:parts.append('No progress recorded; inspect partial edits.')
        parts.append('Next: reclaim task/files, inspect partial edits, resume unchecked work; retain recorded verification unless changes invalidate it.')
        continuity='\n'.join(parts)[:2000]
        from store import task_dict
        import context_tools
        current=task_dict(db.execute('SELECT * FROM tasks WHERE id=?',(task['id'],)).fetchone(),'')
        db.execute('UPDATE tasks SET continuity=?,continuity_basis=? WHERE id=?',(continuity,context_tools.basis(current),task['id']))
        activity(db,task['id'],actor,'comment','Paused: '+why+'. Resume from task continuity and recorded evidence.')
    if tasks:
        db.execute('INSERT INTO project_messages(project,actor,text,created) VALUES(?,?,?,?)',(project,actor,('Paused: '+why+'. Resume task continuity: '+', '.join(str(t['number']) for t in tasks))[:2000],stamp))
    return len(tasks)

def operation(db, action, a, project, actor_user, required, now, activity):
    if action=='agent_status':
        return summary(db,project)
    actor=actor_user(db,a)
    db.execute('BEGIN IMMEDIATE')
    if action=='project_control':
        if actor['kind']!='human':
            raise ValueError('Only a human profile can Stop or Wakeup a project')
        mode=a.get('action')
        if mode not in ['stop','wakeup']:
            raise ValueError('action must be stop or wakeup')
        previous=control(db,project)
        # Wakeup requests are consumed by an optional external bridge, not a model polling loop.
        generation=previous['generation']+1
        why=required(a.get('reason','Human requested '+mode),'reason',500)
        db.execute('INSERT INTO project_control(project,paused,generation,reason,changed,actor) VALUES(?,?,?,?,?,?) ON CONFLICT(project) DO UPDATE SET paused=excluded.paused,generation=excluded.generation,reason=excluded.reason,changed=excluded.changed,actor=excluded.actor',(project,int(mode=='stop'),generation,why,time.time(),actor['id']))
        db.execute("UPDATE agent_sessions SET resume_reset=NULL,auto_resume_status='cancelled by human control' WHERE project=?",(project,))
        result={'control':control(db,project),'delivery':'Cooperative checkpoint or connected CLI bridge; does not wake unrelated desktop chats'}
        if mode=='wakeup':
            result['signal_recorded']=True
            result['wakeup']=summary(db,project)['wakeup']
        return result
    if actor['kind']!='agent':
        raise ValueError('agent_checkpoint requires an agent profile')
    mode=a.get('action','check')
    if mode not in ['check','pause','offline','observe']:
        raise ValueError('action must be check, pause, offline or observe')
    client=a.get('client')
    if client not in ['codex','claude']:
        raise ValueError('client must be codex or claude')
    old=db.execute('SELECT * FROM agent_sessions WHERE project=? AND actor=?',(project,actor['id'])).fetchone()
    db.execute('INSERT INTO agent_sessions(project,actor,client,heartbeat) VALUES(?,?,?,?) ON CONFLICT(project,actor) DO UPDATE SET client=excluded.client,heartbeat=excluded.heartbeat',(project,actor['id'],client,time.time()))
    fields=['session_used_percent','weekly_used_percent','session_resets_at','weekly_resets_at']
    if any(k in a for k in fields):
        for key in fields:
            value=a.get(key)
            if value is not None and (type(value) not in [int,float] or not math.isfinite(value) or value<0 or ('percent' in key and value>100)):
                raise ValueError('Invalid provider usage value')
            db.execute(f'UPDATE agent_sessions SET {key}=? WHERE project=? AND actor=?',(value,project,actor['id']))
        observed=a.get('observed_at',time.time())
        if type(observed) not in [int,float] or not math.isfinite(observed) or observed<0 or observed>time.time()+5:
            raise ValueError('Invalid usage observation timestamp')
        db.execute('UPDATE agent_sessions SET usage_at=? WHERE project=? AND actor=?',(observed,project,actor['id']))
    why=reason(db,project,actor['id'])
    if mode in ['pause','offline']:
        why=required(a.get('reason','Session paused' if mode=='pause' else 'Session closed'),'reason',500)
    if mode=='observe':
        if type(a.get('running')) is not bool:
            raise ValueError('Bridge observe requires a boolean running')
        db.execute('UPDATE agent_sessions SET bridge_heartbeat=? WHERE project=? AND actor=?',(time.time(),project,actor['id']))
        if a['running'] and db.execute("SELECT 1 FROM sqlite_master WHERE name='worker_launch_offers'").fetchone():
            db.execute('UPDATE worker_launch_offers SET expires=? WHERE project=? AND actor=?',(time.time()+600,project,actor['id']))
        status=('stopping' if why else 'working') if a['running'] else 'waiting'
    else:
        status='offline' if mode=='offline' else 'paused' if why else 'working'
    text=a.get('summary','')
    if 'run_exit_code' in a:
        if type(a['run_exit_code']) is not int:raise ValueError('run_exit_code must be an integer')
        run_error=a.get('run_error','')
        if not isinstance(run_error,str) or len(run_error)>1000:raise ValueError('run_error must be text, max1000')
        db.execute('UPDATE agent_sessions SET run_exit_code=?,run_error=? WHERE project=? AND actor=?',(a['run_exit_code'],run_error,project,actor['id']))
    elif mode=='observe' and a.get('running'):
        db.execute("UPDATE agent_sessions SET run_exit_code=NULL,run_error='' WHERE project=? AND actor=?",(project,actor['id']))
    if not isinstance(text,str) or len(text)>2000:
        raise ValueError('summary must be text, max2000')
    released=handoff(db,project,actor['id'],why,text,now,activity) if why and mode!='observe' else 0
    resume=quota_resume(db,project,actor['id'],mode,why,old,a.get('running',False))
    db.execute('UPDATE agent_sessions SET status=?,reason=? WHERE project=? AND actor=?',(status,why,project,actor['id']))
    usage_error=''
    if 'cli_usage_report' in a:
        import execution_usage
        try:execution_usage.record(db,project,actor['id'],a['client'],a['cli_usage_report'])
        except ValueError as error:usage_error=str(error)
    return {'should_stop':bool(why),'reason':why,'status':status,'tasks_released':released,**({'cli_usage_error':usage_error} if usage_error else {}),'control':control(db,project),'auto_resume':resume,'check_after_seconds':db.execute('SELECT usage_check_seconds FROM projects WHERE id=?',(project,)).fetchone()[0]}
