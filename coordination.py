"""Project policy and advisory file reservations shared by the web and MCP."""
import hashlib
import git_worktrees
import os
import secrets
import time
from pathlib import Path

from git_sync import GIT_DEFAULTS, validate as validate_git
POLICY_DEFAULTS = {'workflow_mode':'Auto', 'task_sizing':'Small', 'session_stop_percent':92, 'weekly_stop_percent':94, 'usage_guard':True, 'usage_check_seconds':60, 'auto_resume_session_quota':False,'max_parallel_agents':1, **GIT_DEFAULTS}

def validate_policy(changed):
    validate_git(changed)
    if 'max_parallel_agents' in changed and (type(changed['max_parallel_agents']) is not int or not 1<=changed['max_parallel_agents']<=20):
        raise ValueError('max_parallel_agents must be 1..20')
    if 'workflow_mode' in changed and changed['workflow_mode'] not in ['Auto','Light','Standard']:
        raise ValueError('workflow_mode must be Auto, Light or Standard')
    if 'task_sizing' in changed and changed['task_sizing'] not in ['Small','Balanced','Unrestricted']:
        raise ValueError('task_sizing must be Small, Balanced or Unrestricted')
    for key in ['session_stop_percent','weekly_stop_percent','usage_check_seconds']:
        if key in changed and (type(changed[key]) is not int or not 1 <= changed[key] <= (300 if key=='usage_check_seconds' else 100)):
            raise ValueError(key+' must be an integer within its allowed range')
    if 'usage_guard' in changed and type(changed['usage_guard']) not in [bool,int]:
        raise ValueError('usage_guard must be boolean')
    if 'usage_guard' in changed and changed['usage_guard'] not in [True,False]:
        raise ValueError('usage_guard must be boolean')
    if 'auto_resume_session_quota' in changed and (type(changed['auto_resume_session_quota']) not in (bool,int) or changed['auto_resume_session_quota'] not in (0,1)):
        raise ValueError('auto_resume_session_quota must be boolean')


def validate_scope(size, reason):
    if size not in ('Unknown','Small','Large') or not isinstance(reason,str) or len(reason)>500:
        raise ValueError('Invalid scope estimate/reason')
    if size!='Unknown' and not reason.strip():
        raise ValueError('A scope estimate needs a concise reason')


def migrate(db, separate=False):
    columns = {r['name'] for r in db.execute('PRAGMA table_info(projects)')}
    for name, spec in [('max_active_per_agent', 'INTEGER NOT NULL DEFAULT 1'), ('require_evidence', 'INTEGER NOT NULL DEFAULT 1'), ('workspace_root', "TEXT NOT NULL DEFAULT ''"), ('settings_version', 'INTEGER NOT NULL DEFAULT 1')]:
        if name not in columns:
            db.execute(f'ALTER TABLE projects ADD COLUMN {name} {spec}')
    for name,value in POLICY_DEFAULTS.items():
        if name not in columns:
            spec = f"TEXT NOT NULL DEFAULT '{value}'" if isinstance(value,str) else f'INTEGER NOT NULL DEFAULT {int(value)}'
            db.execute(f'ALTER TABLE projects ADD COLUMN {name} {spec}')
    columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
    for name, spec in [('files', "TEXT NOT NULL DEFAULT '[]'"), ('evidence', "TEXT NOT NULL DEFAULT ''")]:
        if name not in columns:
            db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {spec}')
    namespace = 'reservations.' if separate else ''
    references = '' if separate else ' REFERENCES projects(id)'
    task_ref = '' if separate else ' REFERENCES tasks(id)'
    user_ref = '' if separate else ' REFERENCES users(id)'
    db.execute(f'''CREATE TABLE IF NOT EXISTS {namespace}file_locks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
        project INTEGER NOT NULL{references}, task INTEGER NOT NULL{task_ref},
        owner_id INTEGER NOT NULL{user_ref}, token_hash TEXT NOT NULL,
        lock_until REAL NOT NULL, created TEXT NOT NULL)''')
    db.execute(f'CREATE INDEX IF NOT EXISTS {namespace}file_lock_owner ON file_locks(owner_id,id)')
    db.execute(f'CREATE INDEX IF NOT EXISTS {namespace}file_lock_task ON file_locks(task)')
    import runtime
    runtime.migrate(db,separate)


def settings(row):
    return {k: bool(row[k]) if k in ['require_evidence','usage_guard','auto_resume_session_quota','git_cleanup'] else row[k] for k in ['max_active_per_agent','require_evidence','workspace_root','settings_version',*POLICY_DEFAULTS]}


def active_count(db, project, actor, exclude=-1):
    return db.execute("SELECT COUNT(*) FROM tasks WHERE project=? AND owner_id=? AND status='In Progress' AND lock_until>? AND id<>?", (project, actor, time.time(), exclude)).fetchone()[0]


def check_capacity(db, project, actor, task):
    user = db.execute('SELECT kind FROM users WHERE id=?', (actor,)).fetchone()
    limit = db.execute('SELECT max_active_per_agent FROM projects WHERE id=?', (project,)).fetchone()[0]
    if user and user['kind'] == 'agent' and active_count(db,project,actor,task) >= limit:
        raise ValueError(f'Agent active task limit reached ({limit}); finish or release current work first')
    if user and user['kind']=='agent' and not parallel_available(db,project,actor):
        raise ValueError('Project parallel-agent limit reached; defer without starting a model')

def parallel_available(db,project,actor):
    actors={r[0] for r in db.execute("SELECT DISTINCT t.owner_id FROM tasks t JOIN users u ON u.id=t.owner_id WHERE t.project=? AND u.kind='agent' AND t.status='In Progress' AND t.lock_until>?",(project,time.time()))}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='worker_launch_offers'").fetchone():
        actors.update(r[0] for r in db.execute('SELECT o.actor FROM worker_launch_offers o JOIN users u ON u.id=o.actor WHERE o.project=? AND o.expires>? AND u.active=1',(project,time.time())))
    limit=db.execute('SELECT max_parallel_agents FROM projects WHERE id=?',(project,)).fetchone()[0]
    return actor in actors or len(actors)<limit


def file_plan(value):
    if not isinstance(value,list) or len(value)>100 or any(not isinstance(p,str) or not p.strip() or len(p)>1024 or '\x00' in p for p in value):
        raise ValueError('files must contain at most 100 nonempty file paths, max 1024 characters each')
    return list(dict.fromkeys(p.strip() for p in value))


def canonical_paths(root, paths):
    paths=file_plan(paths)
    if not paths:
        return []
    if not root:
        raise ValueError('Set project workspace_root before reserving files')
    base=Path(root).resolve()
    result=[]
    for value in paths:
        path=Path(value)
        path=(path if path.is_absolute() else base/path).resolve()
        path=git_worktrees.logical(base,path)
        try:
            path.relative_to(base)
        except ValueError:
            raise ValueError('File path escapes the project workspace') from None
        if path == base or path.is_dir():
            raise ValueError('Reserve individual files, not directories')
        result.append(os.path.normcase(str(path)))
    return list(dict.fromkeys(result))


def conflicts(db, paths, task, actor):
    result=[]
    for path in paths:
        row=db.execute('SELECT id,path,project,task,owner_id,lock_until FROM file_locks WHERE path=? AND lock_until>?', (path,time.time())).fetchone()
        if row and (row['task'] != task or row['owner_id'] != actor):
            result.append(dict(row))
    return result


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def reserve(db, paths, project, task, actor, token, until, created):
    for path in paths:
        db.execute('DELETE FROM file_locks WHERE path=? AND lock_until<=?', (path,time.time()))
        db.execute('''INSERT INTO file_locks(path,project,task,owner_id,token_hash,lock_until,created) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET token_hash=excluded.token_hash,lock_until=excluded.lock_until''', (path,project,task,actor,digest(token),until,created))


def renew_files(db, task, token, until):
    # Old-generation reservations must not be adopted after a task lock expires.
    db.execute('DELETE FROM file_locks WHERE task=? AND (token_hash<>? OR lock_until<=?)', (task,digest(token),time.time()))
    db.execute('UPDATE file_locks SET lock_until=? WHERE task=? AND token_hash=?', (until,task,digest(token)))


def own_task(db, a):
    row=db.execute('SELECT * FROM tasks WHERE id=? AND project=?', (a.get('id'),a['project'])).fetchone()
    if not row:
        raise ValueError('Task not found')
    if row['status'] != 'In Progress' or row['owner_id'] != a.get('actor') or row['lock_until'] <= time.time() or not secrets.compare_digest(row['lock_token'] or '', a.get('token') or ''):
        raise ValueError('An active task claim with its actor and token is required')
    return row


def resumable(db, project_row, actor, limit=5):
    """Own unfinished work left by an earlier session: resume it, never redo it."""
    from store import project_prefix
    prefix=project_prefix(project_row['name'])
    rows=db.execute("SELECT * FROM tasks WHERE project=? AND owner_id=? AND status IN ('In Progress','TODO','Blocked','In Review') AND (status='In Progress' OR continuity!='') ORDER BY updated DESC,id DESC LIMIT ?",(project_row['id'],actor,limit)).fetchall()
    result=[]
    for r in rows:
        item={'id':r['id'],'code':f"{prefix}-{r['number']}",'title':r['title'],'status':r['status'],'continuity':(r['continuity'] or '')[:400]}
        if r['status']=='In Progress' and r['lock_until']<=time.time():
            # Crash or expired claim without a handoff: report the edits on disk.
            import git_changes,json
            item['interrupted']=True
            item['edits']=git_changes.summary(git_worktrees.inspection_root(project_row['workspace_root'],git_worktrees.identity(r)),json.loads(r['files'] or '[]'))
        result.append(item)
    return result

def page_locks(db, project=None, actor=None, after=0, limit=20):
    if type(limit) is not int or not 1<=limit<=100 or type(after) is not int or after<0:
        raise ValueError('Use a nonnegative after cursor and limit 1..100')
    where, params=['f.lock_until>?','f.id>?'],[time.time(),after]
    for field,value in [('project',project),('owner_id',actor)]:
        if value is not None:
            where.append(f'f.{field}=?')
            params.append(value)
    sql='''SELECT f.id,f.path,f.project,f.task,f.owner_id,u.name AS owner_name,f.lock_until,f.created,t.number,p.name AS project_name
        FROM file_locks f JOIN users u ON u.id=f.owner_id JOIN tasks t ON t.id=f.task JOIN projects p ON p.id=f.project WHERE '''+' AND '.join(where)
    rows=[dict(r) for r in db.execute(sql+' ORDER BY f.id LIMIT ?', (*params,limit+1))]
    more=len(rows)>limit
    rows=rows[:limit]
    return {'items':rows,'has_more':more,'next_cursor':rows[-1]['id'] if rows else after}


def operation(db, action, a, project_row, actor_user, activity, now):
    project=a['project']
    if action == 'project_settings':
        changed={k:a[k] for k in ['max_active_per_agent','require_evidence','workspace_root',*POLICY_DEFAULTS] if k in a}
        if not changed:
            return settings(project_row)
        if actor_user(db,a)['kind'] != 'human':
            raise ValueError('Only a human profile can change project settings')
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
        validate_policy(changed)
        if 'max_active_per_agent' in changed:
            limit=changed['max_active_per_agent']
            if type(limit) is not int or not 1<=limit<=20:
                raise ValueError('max_active_per_agent must be 1..20')
            maximum=db.execute("SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM tasks t JOIN users u ON u.id=t.owner_id WHERE t.project=? AND u.kind='agent' AND t.status='In Progress' AND t.lock_until>? GROUP BY t.owner_id)",(project,time.time())).fetchone()[0] or 0
            if maximum>limit:
                raise ValueError('Release excess active tasks before lowering the limit')
        if 'require_evidence' in changed and type(changed['require_evidence']) is not bool:
            raise ValueError('require_evidence must be boolean')
        if 'git_mode' in changed and changed['git_mode']!=current['git_mode'] and db.execute("SELECT 1 FROM tasks WHERE project=? AND status='In Progress' AND lock_until>?",(project,time.time())).fetchone():
            raise ValueError('Finish or safely pause active tasks before changing Git workspace mode')
        if 'git_mode' in changed and current['git_mode']=='Worktrees' and changed['git_mode']!='Worktrees':
            saved=git_worktrees.entries(current['workspace_root'])
            if any(git_worktrees.identity(r) in saved for r in db.execute("SELECT * FROM tasks WHERE project=? AND status NOT IN ('Done','Cancelled')",(project,))):
                raise ValueError('Complete or cancel tasks with saved workspaces before leaving Worktrees mode')
        if 'workspace_root' in changed:
            root=changed['workspace_root']
            if not isinstance(root,str) or len(root)>1024 or '\x00' in root or (root and not Path(root).is_absolute()):
                raise ValueError('workspace_root must be an absolute directory path or empty')
            root=str(Path(root).resolve()) if root else ''
            if root and Path(root).exists() and not Path(root).is_dir():
                raise ValueError('workspace_root must be a directory')
            if root != current['workspace_root'] and db.execute('SELECT 1 FROM file_locks WHERE project=? AND lock_until>?',(project,time.time())).fetchone():
                raise ValueError('Release live file locks before changing workspace_root')
            changed['workspace_root']=root
        for field,value in changed.items():
            db.execute(f'UPDATE projects SET {field}=? WHERE id=?',(value,project))
        if changed.get('auto_resume_session_quota') is False:
            db.execute("UPDATE agent_sessions SET resume_reset=NULL,auto_resume_status='disabled' WHERE project=?",(project,))
        db.execute('UPDATE projects SET settings_version=settings_version+1 WHERE id=?',(project,))
        return settings(db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone())
    if action == 'session_resume':
        actor=actor_user(db,a)
        return {'settings':settings(project_row),'active_tasks':active_count(db,project,actor['id']),
                'own_file_locks':page_locks(db,project=project,actor=actor['id'],after=a.get('after',0),limit=a.get('limit',20)),
                'resume':resumable(db,project_row,actor['id'])}
    mode=a.get('action','list')
    if mode=='list':
        if a.get('own'):
            actor_user(db,a)
        return page_locks(db,project, a.get('actor') if a.get('own') else None, a.get('after',0), a.get('limit',20))
    if mode not in ['acquire','release']:
        raise ValueError('action must be list, acquire, or release')
    actor=actor_user(db,a)
    db.execute('BEGIN IMMEDIATE')
    if mode=='acquire':
        import runtime
        runtime.gate(db,project,actor['id'])
        task=own_task(db,a)
        paths=canonical_paths(db.execute('SELECT workspace_root FROM projects WHERE id=?',(project,)).fetchone()[0],a.get('paths',[]))
        if not paths:
            raise ValueError('Supply paths to acquire')
        held=conflicts(db,paths,task['id'],actor['id'])
        if held:
            return {'acquired':False,'conflicts':held[:20],'conflicts_total':len(held)}
        reserve(db,paths,project,task['id'],actor['id'],a['token'],task['lock_until'],now())
        return {'acquired':True,'count':len(paths),'lock_until':task['lock_until']}
    force=a.get('force',False)
    if force and actor['kind']!='human':
        raise ValueError('Only a human can force-release another worker\'s files')
    ids=a.get('ids',[])
    if not isinstance(ids,list) or len(ids)>100 or any(type(i) is not int for i in ids):
        raise ValueError('ids must be a list of at most 100 file-lock IDs')
    if a.get('all') and ids:
        raise ValueError('Use all or ids, not both')
    if not a.get('all') and not ids:
        raise ValueError('Supply ids or all=true')
    where,params=['project=?'],[project]
    if not force:
        where.append('owner_id=?')
        params.append(actor['id'])
    if ids:
        where.append('id IN ('+','.join('?' for _ in ids)+')')
        params+=ids
    rows=db.execute('SELECT * FROM file_locks WHERE '+' AND '.join(where),params).fetchall()
    if ids and len(rows)!=len(set(ids)):
        raise ValueError('A lock is missing, belongs to another project, or is owned by another worker')
    for task in {r['task'] for r in rows}:
        activity(db,task,actor['id'],'files',f"{'Human force-released' if force else 'Released'} file reservations ({sum(r['task']==task for r in rows)}).")
    db.execute('DELETE FROM file_locks WHERE '+' AND '.join(where),params)
    return {'released':len(rows),'scope':'project' if force else 'own','project':project}


def workflow_mode(db, project, task=None):
    """Conservative eligibility; mode never weakens reservations or quotas."""
    import json
    policy=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
    configured=policy['workflow_mode']
    why='Small single-agent task'
    if configured=='Standard':
        why='Standard selected by project policy'
    elif policy['task_sizing']!='Small':
        why='Task sizing is not Small'
    else:
        workers={r[0] for r in db.execute("SELECT actor FROM agent_sessions WHERE project=? AND status IN ('working','stopping') AND heartbeat>?",(project,time.time()-max(90,policy['usage_check_seconds']*2)))}
        workers.update(r[0] for r in db.execute('SELECT owner_id FROM tasks WHERE project=? AND lock_until>?',(project,time.time())))
        live=db.execute('SELECT COUNT(*) FROM tasks WHERE project=? AND lock_until>?',(project,time.time())).fetchone()[0]
        if len(workers)>1 or live>1:
            why='Concurrent workers or multiple active claims'
        elif db.execute("SELECT 1 FROM tasks WHERE project=? AND status='Blocked' LIMIT 1",(project,)).fetchone():
            why='Blocked work requires assessment'
        else:
            row=db.execute('SELECT * FROM tasks WHERE project=? AND id=?',(project,task)).fetchone() if task else db.execute("SELECT * FROM tasks WHERE project=? AND status IN ('In Progress','TODO') ORDER BY CASE status WHEN 'In Progress' THEN 0 ELSE 1 END,priority,id LIMIT 1",(project,)).fetchone()
            if not row:
                why='No actionable task'
            elif row['status'] not in ['TODO','In Progress'] or json.loads(row['dependencies']) or row['blocked_reason'].strip():
                why='Task has dependencies or is not actionable'
            elif row['scope_size']=='Large':
                why='Task scope estimated Large'
            elif row['scope_size']!='Small' and (len(json.loads(row['files']))>5 or len(json.loads(row['checklist']))>5 or len(json.loads(row['acceptance_criteria']))>5 or len(row['description'])>4000):
                why='Task exceeds lightweight scope bounds'
    return {'configured':configured,'effective':'Light' if why=='Small single-agent task' else 'Standard','reason':why}
