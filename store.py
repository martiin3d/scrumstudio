import json
import git_worktrees
import os
import sqlite3
import subprocess
import unicodedata
import time
import secrets
import coordination as coord
import telemetry
import runtime
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUSES = telemetry.STATUSES

def connect(path=None, locks=None):
    path = Path(path or os.environ.get('SCRUM_DB', ROOT / 'data' / 'scrum.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if locks is not None:
        db.execute('ATTACH DATABASE ? AS reservations', (str(locks),))
    db.executescript('''
    CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, project INTEGER NOT NULL REFERENCES projects(id), title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'TODO', priority INTEGER NOT NULL DEFAULT 2, blocked_reason TEXT NOT NULL DEFAULT '', dependencies TEXT NOT NULL DEFAULT '[]', updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, project INTEGER NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, title TEXT NOT NULL, items TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, name TEXT NOT NULL, kind TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS task_activity(id INTEGER PRIMARY KEY, task INTEGER NOT NULL REFERENCES tasks(id), actor INTEGER REFERENCES users(id), kind TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS activity_task_id ON task_activity(task,id);
    CREATE TABLE IF NOT EXISTS project_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, project INTEGER NOT NULL REFERENCES projects(id), actor INTEGER NOT NULL REFERENCES users(id), recipient INTEGER REFERENCES users(id), task INTEGER REFERENCES tasks(id), text TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS messages_project_id ON project_messages(project,id);
    CREATE INDEX IF NOT EXISTS tasks_project_status ON tasks(project,status);
    ''')
    if 'checklist' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
        try:
            db.execute("ALTER TABLE tasks ADD COLUMN checklist TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            if 'checklist' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                raise
    # Serialize migration so a web request and MCP startup cannot number twice.
    with db:
        db.execute('BEGIN IMMEDIATE')
        if 'task' not in {r['name'] for r in db.execute('PRAGMA table_info(notes)')}:
            db.execute('ALTER TABLE notes ADD COLUMN task INTEGER REFERENCES tasks(id)')
        db.execute('CREATE INDEX IF NOT EXISTS notes_task_scope ON notes(project,task,kind,id)')
        if 'audience' not in {r['name'] for r in db.execute('PRAGMA table_info(notes)')}:
            db.execute("ALTER TABLE notes ADD COLUMN audience TEXT NOT NULL DEFAULT 'human'")
        db.execute('CREATE INDEX IF NOT EXISTS notes_ai_pending ON notes(project,task,audience,done,id)')
        if 'number' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
            db.execute('ALTER TABLE tasks ADD COLUMN number INTEGER')
            counts = {}
            for row in db.execute('SELECT id,project FROM tasks ORDER BY id').fetchall():
                counts[row['project']] = counts.get(row['project'], 0) + 1
                db.execute('UPDATE tasks SET number=? WHERE id=?', (counts[row['project']], row['id']))
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS task_project_number ON tasks(project,number)')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
        if 'blocked_reason_kind' not in columns:
            # Old prose is ambiguous: never silently dismiss a possible external constraint.
            db.execute("ALTER TABLE tasks ADD COLUMN blocked_reason_kind TEXT NOT NULL DEFAULT 'external'")
        for name, spec in [('owner_id', 'INTEGER REFERENCES users(id)'), ('lock_token', 'TEXT'), ('lock_until', 'REAL NOT NULL DEFAULT 0'), ('category', "TEXT NOT NULL DEFAULT 'General'")]:
            if name not in columns:
                db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {spec}')
        if 'acceptance_criteria' not in columns:
            db.execute("ALTER TABLE tasks ADD COLUMN acceptance_criteria TEXT NOT NULL DEFAULT '[]'")
        for field in ('continuity','continuity_basis'):
            if field not in columns:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        for field,default in [('context_group',''),('scope_size','Unknown'),('scope_reason',''),('verification_level','auto'),('verification_reason','')]:
            if field not in columns:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {field} TEXT NOT NULL DEFAULT '{default}'")
        for field,default in [('model',''),('model_tier','auto'),('model_reason',''),('model_selection','{}')]:
            if field not in columns:db.execute(f"ALTER TABLE tasks ADD COLUMN {field} TEXT NOT NULL DEFAULT '{default}'")
        if 'attachments' not in columns:
            db.execute("ALTER TABLE tasks ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
        if 'cost_data' not in columns:
            db.execute("ALTER TABLE tasks ADD COLUMN cost_data TEXT NOT NULL DEFAULT '{\"estimated_tokens\":null,\"estimated_quota_percent\":null,\"reports\":[]}'")
        if 'active' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        coord.migrate(db, separate=locks is not None)
        telemetry.migrate(db)
    return db

def validate_context_group(value=''):
    if not isinstance(value,str) or len(value)>80 or any(ord(c)<32 for c in value):
        raise ValueError('context_group must be a single line of text, max 80 characters')
    return value.strip()


def validate_verification(level='auto', reason=''):
    if not isinstance(level,str) or level not in ('auto','minimal','targeted','full'):
        raise ValueError('Invalid verification_level')
    if not isinstance(reason,str) or len(reason)>500:
        raise ValueError('verification_reason must be text, max 500 characters')
    if level!='auto' and not reason.strip():
        raise ValueError('Explicit verification intent needs a concise rationale')


def now():
    return datetime.now(timezone.utc).isoformat()

def required(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must be nonempty text (max {limit})')
    return value.strip()

def project_exists(db, project):
    row = db.execute('SELECT * FROM projects WHERE id=?', (project,)).fetchone()
    if not row:
        raise ValueError('Project not found')
    return row

def git_claim(db, project, row, prefix, actor):
    """Branch policy at claim time; returns a report or a {'busy':...} refusal."""
    import git_sync
    project_row=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
    if not git_sync.enabled(coord.settings(project_row)):return None
    if coord.settings(project_row).get('git_mode')=='Worktrees':
        import storage
        storage.ensure_uids(db)
        row=db.execute('SELECT * FROM tasks WHERE id=?',(row['id'],)).fetchone()
    task={'id':row['id'],'uid':row['uid'] if 'uid' in row.keys() else None,'code':f"{prefix}-{row['number']}",'title':row['title'],'context_group':row['context_group'] if 'context_group' in row.keys() else ''}
    settings=coord.settings(project_row)
    active=db.execute("SELECT * FROM tasks WHERE project=? AND id!=? AND status='In Progress' AND lock_until>?",(project,row['id'],time.time())).fetchall()
    if settings.get('git_mode')=='Worktrees':
        if row['category']=='Git recovery' and active:
            return {'busy':{'current_branch':'canonical','wanted':'Git recovery','reason':'Finish active work before Git recovery','dirty':[],'dirty_total':0}}
        desired=git_worktrees.branch(project_row['workspace_root'],git_worktrees.identity(row)) or git_sync.branch_for(settings,task)
        if any((git_worktrees.branch(project_row['workspace_root'],git_worktrees.identity(r)) or git_sync.branch_for(settings,{'code':f"{prefix}-{r['number']}",'title':r['title'],'context_group':r['context_group']}))==desired for r in active):
            return {'busy':{'current_branch':desired,'wanted':desired,'reason':'Another active task owns this context-group workspace','dirty':[],'dirty_total':0}}
        current=git_sync.state(project_row['workspace_root'])
        if active and (any(r['category']=='Git recovery' for r in active) or (current and current[0]==desired and not git_worktrees.branch(project_row['workspace_root'],git_worktrees.identity(row)))):
            return {'busy':{'current_branch':current[0] if current else 'canonical','wanted':desired,'reason':'Finish active work before reusing the canonical checkout or running Git recovery','dirty':[],'dirty_total':0}}
        active=[]
    # Even a clean working tree belongs to active workers; never switch it.
    if any(git_sync.branch_for(settings,{'code':f"{prefix}-{r['number']}",'title':r['title'],'context_group':r['context_group']})!=git_sync.branch_for(settings,task) for r in active):
        current=git_sync.state(project_row['workspace_root'])
        dirty=current[1] if current else []
        wanted=git_sync.branch_for(settings,task)
        activity(db,row['id'],actor,'comment','git: claim refused, another active task owns the checkout branch (wanted '+wanted+').')
        return {'busy':{'current_branch':current[0] if current else 'unknown','wanted':wanted,'reason':'Another active task owns this checkout branch','dirty':dirty[:20],'dirty_total':len(dirty)}}
    if row['category']=='Git recovery':
        if active:raise ValueError('Finish active work before Git recovery')
        current=git_sync.state(project_row['workspace_root'])
        return {'branch':current[0] if current else 'unknown','recovery':True}
    task['allow_parent_refresh']=not active
    task['allow_checkpoint_restore']=not active
    task['dependencies']=[{'code':f"{prefix}-{r['number']}",'title':r['title'],'context_group':r['context_group']} for r in db.execute('SELECT number,title,context_group FROM tasks WHERE project=? AND id IN ('+','.join('?' for _ in json.loads(row['dependencies']))+')',[project,*json.loads(row['dependencies'])])] if json.loads(row['dependencies']) else []
    report=git_sync.on_claim(project_row['workspace_root'],settings,task)
    if report and report.get('refreshed_from'):
        activity(db,row['id'],actor,'comment','git: synchronized '+report['branch']+' with its recorded parent '+report['refreshed_from']+' before claiming; no model recovery needed.')
    if report and report.get('busy'):
        activity(db,row['id'],actor,'comment','git: claim refused, checkout has uncommitted work on branch '+report['busy']['current_branch']+' (wanted '+report['busy']['wanted']+').')
    elif report and (report.get('created') or report.get('switched') or report.get('error')):
        activity(db,row['id'],actor,'comment','git: '+('created' if report.get('created') else 'switched to' if report.get('switched') else 'ERROR')+' branch '+report['branch']+(' ('+report['error']+')' if report.get('error') else '')+'.')
    return report

def git_done(db, project, t, prefix, actor):
    """Commit/push policy when a task first becomes Done."""
    import git_sync
    project_row=db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
    settings=coord.settings(project_row)
    if not git_sync.enabled(settings) or t.get('category')=='Git recovery':return None
    group=(t.get('context_group') or '').strip()
    members=[];group_done=True
    if group:
        members=[{'id':r['id'],'code':f"{prefix}-{r['number']}",'status':r['status'],'files':json.loads(r['files'])} for r in db.execute('SELECT id,number,status,files FROM tasks WHERE project=? AND context_group=? ORDER BY id',(project,group))]
        group_done=all(m['status'] in ('Done','Cancelled') for m in members)
    task={'id':t['id'],'code':t['code'],'title':t['title'],'files':t.get('files') or [],'context_group':group}
    if git_sync.integrated_completion(settings):
        task['completion_basis']=git_sync.completion_basis(dict(db.execute('SELECT * FROM tasks WHERE id=?',(t['id'],)).fetchone()))
    dependency_checkpoint=False
    if group and settings.get('git_merge')=='Group' and (settings.get('git_commit')=='Task' or group_done):
        members_done={m['id'] for m in members if m['status']=='Done'}
        source=git_sync.branch_for(settings,task)
        for dependent in db.execute("SELECT id,number,title,context_group,dependencies FROM tasks WHERE project=? AND status IN ('TODO','Blocked')",(project,)):
            deps=json.loads(dependent['dependencies'])
            other={'code':f"{prefix}-{dependent['number']}",'title':dependent['title'],'context_group':dependent['context_group']}
            if members_done.intersection(deps) and git_sync.branch_for(settings,other)!=source:
                dependency_checkpoint=True;break
    root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(t))
    task['git_branch']=git_worktrees.branch(project_row['workspace_root'],git_worktrees.identity(t))
    report=git_sync.on_done(db,root,settings,task,project,actor,group_done,members,dependency_checkpoint=dependency_checkpoint)
    # The Git job runs after this database transaction commits.
    if report and not report.get('queued'):activity(db,t['id'],actor,'comment',git_sync.describe(report))
    return report

def project_prefix(name):
    letters = ''.join(c for c in unicodedata.normalize('NFKD', name) if c.isalpha() and not unicodedata.combining(c))
    return (letters[:3] or 'PRJ').upper()

def priority(value):
    if type(value) is not int or value not in [1, 2, 3]:
        raise ValueError('Priority must be 1, 2, or 3')
    return value

def description(value):
    if not isinstance(value, str) or len(value) > 12000:
        raise ValueError('Description must be text, max 12000 characters')
    return value

def task_dict(row, prefix, include_cost=False):
    t = dict(row)
    t['dependencies'] = json.loads(t['dependencies'])
    t['checklist'] = json.loads(t['checklist'])
    t['files'] = json.loads(t['files'])
    t['attachments'] = json.loads(t['attachments'])
    t['model_selection']=json.loads(t['model_selection'])
    t['acceptance_criteria'] = json.loads(t['acceptance_criteria'])
    import task_cost
    cost=t.pop('cost_data')
    if include_cost:
        t['cost_data']=json.loads(cost)
        t['cost_summary']=task_cost.summary(t['cost_data'])
        t['cost_data'].pop('reports',None)
    t['code'] = f"{prefix}-{t['number']}"
    t.pop('lock_token', None)
    t['locked'] = t['lock_until'] > time.time()
    import context_tools
    return context_tools.decorate(t)

def title_key(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())

def duplicate_task(db, project, title, a):
    """Same-title task for an agent re-planning after a resume.

    Returns (row, differs): differs is empty when the registered outcome is
    identical, otherwise the fields that differ. A reworded re-plan of UNFINISHED
    work must never create a second task silently: the caller reports the
    existing one and the agent continues it, refines it (task_plan with id) or
    passes allow_duplicate. A Done task with a different scope is not a match:
    new requirements under an old title are new work.
    """
    allow=a.get('allow_duplicate',False)
    if type(allow) is not bool:raise ValueError('allow_duplicate must be boolean')
    if allow or a.get('actor') is None or actor_user(db,a)['kind']!='agent':return None,[]
    key=title_key(title)
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status!='Cancelled' ORDER BY id DESC",(project,)):
        if title_key(row['title'])!=key:continue
        differs=[]
        if row['description'].strip()!=str(a.get('description','')).strip():differs.append('description')
        if json.loads(row['acceptance_criteria'])!=a.get('acceptance_criteria'):differs.append('acceptance_criteria')
        for field in ('files','dependencies','checklist'):
            if field in a and json.loads(row[field])!=a[field]:differs.append(field)
        for field in ('category','priority','scope_size','scope_reason','context_group','model','model_tier','model_reason','verification_level','verification_reason'):
            if field in a and row[field]!=a[field]:differs.append(field)
        if differs and row['status']=='Done':
            continue  # delivered work with a new scope is new work; an unfinished one is continued
        return row,differs
    return None,[]

def existing_note(differs):
    if not differs:return 'Identical scope already registered; reuse its recorded state. allow_duplicate=true permits a deliberately separate outcome.'
    return 'A task with this title already exists with a different '+', '.join(differs)+'. Reuse it, refine it with task_plan(id=...) if its scope must change, or pass allow_duplicate=true for a deliberately separate outcome. Nothing was created.'

def actor_user(db, a):
    row = db.execute('SELECT * FROM users WHERE id=?', (a.get('actor'),)).fetchone()
    if not row or not row['active']:
        raise ValueError('Choose/register a user and supply actor ID')
    return row

def lock_access(row, a):
    if row['lock_until'] > time.time():
        if row['owner_id'] != a.get('actor') or not secrets.compare_digest(row['lock_token'] or '', a.get('token') or ''):
            raise ValueError('Task is locked; only the claiming session can change it')

def checklist(value):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('Checklist must be a list of at most 100 items')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'text', 'done'} or type(item['done']) is not bool:
            raise ValueError('Each checklist item needs text and a boolean done')
        result.append({'text': required(item['text'], 'Checklist text', 500), 'done': item['done']})
    return result


def acceptance_criteria(value, allow_empty=False):
    if not isinstance(value,list) or len(value)>100 or (not value and not allow_empty):
        raise ValueError('acceptance_criteria must contain 1..100 explicit criteria')
    return [required(item,'Acceptance criterion',1000) for item in value]

def activity(db, task, actor, kind, text):
    if actor is not None:
        actor_user(db, {'actor': actor})
    cur = db.execute('INSERT INTO task_activity(task,actor,kind,text,created) VALUES(?,?,?,?,?)', (task, actor, kind, text, now()))
    return cur.lastrowid

def dependencies(db, project, task, values):
    if not isinstance(values, list) or len(values) > 100 or any(type(i) is not int or i == task for i in values):
        raise ValueError('Blocked by must contain at most 100 task IDs, without self-dependencies')
    values = list(dict.fromkeys(values))
    previous=db.execute('SELECT dependencies FROM tasks WHERE id=? AND project=?',(task,project)).fetchone()
    previous=set(json.loads(previous['dependencies'])) if previous else set()
    for dep in values:
        dependency=db.execute('SELECT id,status FROM tasks WHERE id=? AND project=?', (dep, project)).fetchone()
        if not dependency:
            raise ValueError('Dependency must belong to this project')
        if dependency['status']=='Cancelled' and dep not in previous:
            raise ValueError('Cannot add a cancelled prerequisite; reopen or replace it first')
    graph = {r['id']: json.loads(r['dependencies']) for r in db.execute('SELECT id,dependencies FROM tasks WHERE project=?', (project,))}
    graph[task] = values
    visited, visiting = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError('Dependencies cannot form a cycle')
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, []):
            visit(child)
        visiting.remove(node)
        visited.add(node)
    visit(task)
    return values

def resolve_dependents(db, project, completed, prefix, actor):
    done = {r['id'] for r in db.execute("SELECT id FROM tasks WHERE project=? AND status IN ('Done','Cancelled')", (project,))}
    prerequisite=db.execute('SELECT number,status FROM tasks WHERE id=?',(completed,)).fetchone()
    changed = []
    import git_sync
    preserve_done=git_sync.enabled(coord.settings(project_exists(db,project)))
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status NOT IN ('Done','In Review','Cancelled')", (project,)).fetchall():
        deps = json.loads(row['dependencies'])
        if completed not in deps or row['lock_until'] > time.time():
            continue
        remaining = [i for i in deps if i not in done]
        status = row['status']
        reason=row['blocked_reason']
        reason_kind=row['blocked_reason_kind']
        cleared_reason=bool(reason.strip() and reason_kind=='dependency' and not remaining)
        if cleared_reason:reason='';reason_kind='external'
        if status == 'Blocked' and not remaining and not reason.strip():
            status = 'TODO'
        retained=[i for i in deps if db.execute('SELECT status FROM tasks WHERE id=?',(i,)).fetchone()[0]!='Cancelled'] if preserve_done else remaining
        db.execute('UPDATE tasks SET dependencies=?,status=?,blocked_reason=?,blocked_reason_kind=?,updated=? WHERE id=?', (json.dumps(retained), status, reason, reason_kind, now(), row['id']))
        telemetry.transition(db,row['id'],status,actor)
        code = f"{prefix}-{row['number']}"
        author = actor_user(db, {'actor': actor})['name'] if actor is not None else 'System'
        resolution='resolved by cancellation' if prerequisite['status']=='Cancelled' else 'resolved by completion'
        dependency_note='dependency completion retained for Git integration' if preserve_done and prerequisite['status']=='Done' else 'dependency removed'
        activity(db, row['id'], actor, 'comment', f"Blocker {prefix}-{prerequisite['number']} {resolution} by {author}; {dependency_note}; remaining task blockers: {len(remaining)}. Status: {status}." + (' Dependency-linked blocker reason cleared.' if cleared_reason else '') + (' Ready to claim.' if status == 'TODO' and not remaining and not reason.strip() else ''))
        changed.append({'id': row['id'], 'code': code, 'status': status, 'remaining': len(remaining)})
    return changed

def reconcile_checkout_blocks(db, project_row, actor):
    """Reopen explicitly transient checkout blockers, never certify completion."""
    import git_sync
    project=project_row['id'];root=project_row['workspace_root']
    rows=db.execute("SELECT * FROM tasks WHERE project=? AND category='Git recovery' AND status='Blocked' AND blocked_reason_kind='checkout' AND lock_until<=? AND owner_id=?",(project,time.time(),actor)).fetchall()
    if not rows or project_row['git_mode']!='Branches' or not root:return
    if db.execute("SELECT 1 FROM tasks WHERE project=? AND status='In Progress' AND lock_until>?",(project,time.time())).fetchone():return
    if any(Path(r['path']).is_relative_to(Path(root).resolve()) for r in db.execute('SELECT path FROM file_locks WHERE lock_until>?',(time.time(),))):return
    current=git_sync.state(root)
    if not current or current[1]:return
    done={r['id'] for r in db.execute("SELECT id FROM tasks WHERE project=? AND status='Done'",(project,))}
    for row in rows:
        if any(dep not in done for dep in json.loads(row['dependencies'])):continue
        db.execute("UPDATE tasks SET status='TODO',blocked_reason='',blocked_reason_kind='external',updated=? WHERE id=?",(now(),row['id']))
        telemetry.transition(db,row['id'],'TODO',actor)
        activity(db,row['id'],actor,'comment','Checkout obstruction no longer present: Git reports a clean working tree on '+current[0]+'. Reopened for verification using saved continuity; acceptance and publication have not been assumed.')


def run(action, a):
    import git_sync
    with git_sync.transaction_jobs() as jobs:
        result=_run(action,a)
    if action=='agent_checkpoint' and a.get('action')=='observe' and isinstance(result,dict):
        candidate=_run('git_recovery_next',{'project':a['project']})
        if candidate:result['git_recovery_id']=candidate['id']
    import idle_signals
    idle_signals.notify(action,a)
    if jobs and isinstance(result,dict):
        pushes=git_sync.flush(run,jobs,result,a.get('project'))
        if pushes:result['git_push']=pushes
    if action=='task_update' and isinstance(result,dict) and result.get('status')=='Done':
        task=_run('task_get',{'project':a['project'],'id':result['id']})
        if task.get('category')=='Git recovery' and task.get('title','').startswith('Resolve Git integration: '):
            source=task['title'][len('Resolve Git integration: '):].split(' into ',1)[0]
            try:result['integration_reconciled']=_run('git_reconcile',{'project':a['project'],'actor':a.get('actor'),'capability':git_sync.FINALIZE_TOKEN,'source':source})
            except (OSError,ValueError,sqlite3.Error,subprocess.SubprocessError) as error:result['integration_reconciliation_error']=str(error)[:300]
    if action=='agent_checkpoint' and a.get('action')=='offline' and 'run_exit_code' in a and isinstance(result,dict):
        policy=_run('project_settings',{'project':a['project']})
        if policy.get('git_cleanup') and policy.get('workspace_root'):
            try:
                cleaned=git_worktrees.cleanup(policy['workspace_root'],a['actor'],lambda source:_run('git_cleanup_pending',{'project':a['project'],'branch':source}))
                if cleaned:result['git_cleanup']=cleaned
            except (OSError,ValueError) as error:result['git_cleanup_error']=str(error)[:300]
    return result

def _run(action, a):
    if action=='project_control' and a.get('action')=='wakeup':
        if type(a.get('start_agents',True)) is not bool or type(a.get('confirm_client_account',False)) is not bool:
            raise ValueError('Wakeup start_agents and confirm_client_account must be booleans')
    if action=='project_control' and a.get('action')=='wakeup' and a.get('start_agents',True):
        import managed_agents
        import storage
        return managed_agents.wakeup(a,run,lambda:storage.dispatch(action,a,legacy_run,connect))
    if action=='mcp_servers':
        import storage,mcp_selection
        _,folder=storage.entry(a['project'])
        mode=a.get('action','list')
        if mode=='update':
            actor=next((u for u in run('users',{}) if u['id']==a.get('actor') and u['active'] and u['kind']=='human'),None)
            if not actor:raise ValueError('An active human must change MCP server selection')
            mcp_selection.save(folder,a.get('client'),a.get('mode'),a.get('servers',[]))
        elif mode!='list':raise ValueError('Use list or update')
        return mcp_selection.listing(folder)
    if action=='agent_manage':
        import managed_agents
        return managed_agents.operation(a,run)
    if action=='agent_checkpoint' and a.get('profile_id'):
        import managed_agents
        return managed_agents.checkpoint(a,run)
    if action == 'coordination_metrics':
        import overhead
        result=overhead.metrics(a['project'],a.get('filter_session'),a.get('task'))
        if a.get('include_cli_usage'):
            result['cli_usage']=run('cli_usage_summary',{'project':a['project']})
        return result
    if action == 'agent_checkpoint':
        import overhead
        a = overhead.cached_usage(a)
    if action in ['work_start', 'work_finish']:
        import workflow
        return workflow.operation(action, a, run)
    import storage
    return storage.dispatch(action, a, legacy_run, connect)


def legacy_run(action, a, path=None, locks=None):
    with closing(connect(path, locks)) as db, db:
        if action == 'workflow_status':
            project_exists(db,a['project'])
            if 'id' in a and not db.execute('SELECT 1 FROM tasks WHERE project=? AND id=?',(a['project'],a['id'])).fetchone():
                raise ValueError('Task not found')
            return coord.workflow_mode(db,a['project'],a.get('id'))
        if action == 'users':
            return [dict(r) for r in db.execute('SELECT * FROM users ORDER BY name,id')]
        if action == 'user_manage':
            db.execute('BEGIN IMMEDIATE')
            actor=actor_user(db,a)
            if actor['kind']!='human':
                raise ValueError('Only an active human can manage users')
            target=db.execute('SELECT * FROM users WHERE id=?',(a.get('id'),)).fetchone()
            if not target:
                raise ValueError('User not found')
            mode=a.get('action')
            if mode not in ['delete','restore']:
                raise ValueError('action must be delete or restore')
            if mode=='delete':
                import storage
                storage.check_user_deletion(db,target)
            db.execute('UPDATE users SET active=? WHERE id=?',(int(mode=='restore'),target['id']))
            return {'id':target['id'],'active':mode=='restore','history_preserved':True}
        if action == 'user_register':
            identity = required(a.get('identity'), 'identity', 160)
            name = required(a.get('name'), 'name', 120)
            kind = a.get('kind')
            if kind not in ['human', 'agent']:
                raise ValueError('kind must be human or agent')
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM users WHERE identity=?', (identity,)).fetchone()
            if old:
                if not old['active']:
                    raise ValueError('User was deleted; a human must restore it explicitly')
                if old['kind'] != kind:
                    raise ValueError('Identity already registered with a different kind')
                return dict(old)
            cur = db.execute('INSERT INTO users(identity,name,kind) VALUES(?,?,?)', (identity, name, kind))
            return {'id': cur.lastrowid, 'identity': identity, 'name': name, 'kind': kind}
        if action == 'projects':
            return [dict(r) for r in db.execute('SELECT * FROM projects ORDER BY id')]
        if action == 'project_create':
            cur = db.execute('INSERT INTO projects(name,description) VALUES(?,?)', (required(a.get('name'), 'name', 120), description(a.get('description', ''))))
            return {'id': cur.lastrowid}
        project = int(a['project'])
        project_row = project_exists(db, project)
        prefix = project_prefix(project_row['name'])
        if action=='project_workspaces':
            if type(a.get('confirm',False)) is not bool:raise ValueError('confirm must be boolean')
            human=actor_user(db,a)
            if human['kind']!='human':raise ValueError('Only a human can archive project workspaces')
            if a.get('confirm'):
                db.execute('BEGIN IMMEDIATE')
                if not runtime.control(db,project)['paused']:raise ValueError('Stop the project before archiving workspaces')
                if db.execute("SELECT 1 FROM tasks WHERE project=? AND lock_until>?",(project,time.time())).fetchone():raise ValueError('Release live task claims before archiving workspaces')
            result=git_worktrees.archive(project_row['workspace_root'],a.get('confirm',False))
            for item in result['workspaces'] if result.get('confirmed') else []:
                for key in item['tasks']:
                    row=db.execute('SELECT * FROM tasks WHERE project=? AND uid=?',(project,key)).fetchone()
                    if not row:continue
                    message='Workspace archived to '+item['archive']+'; branch '+item['branch']+' retained. Resume in the canonical project only after reconciling these files.'
                    activity(db,row['id'],human['id'],'comment',message)
                    if row['status'] not in ('Done','Cancelled'):
                        db.execute('UPDATE tasks SET continuity=?,updated=? WHERE id=?',((message+'\n'+row['continuity'])[:2000],now(),row['id']))
            return result
        if action == 'task_attachment':
            import attachments
            return attachments.operation(db,a,project,path)
        if action == 'task_search':
            import context_tools
            return context_tools.search(db,project,prefix,a)
        if action in ['agent_checkpoint','agent_status','project_control']:
            return runtime.operation(db,action,a,project,actor_user,required,now,activity)
        if action == 'agent_interaction':
            import agent_interactions
            return agent_interactions.operation(db,a,actor_user,now)
        if action == 'project_chat':
            if 'text' in a:
                author = actor_user(db, a)
                recipient = a.get('recipient')
                if recipient is not None:
                    actor_user(db, {'actor': recipient})
                task = a.get('task')
                if task is not None and not db.execute('SELECT 1 FROM tasks WHERE id=? AND project=?', (task, project)).fetchone():
                    raise ValueError('Task must belong to this project')
                cur = db.execute('INSERT INTO project_messages(project,actor,recipient,task,text,created) VALUES(?,?,?,?,?,?)', (project, author['id'], recipient, task, required(a['text'], 'text', 2000), now()))
                return {'id': cur.lastrowid, 'saved': True}
            limit = a.get('limit', 20)
            if type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError('limit must be 1..50')
            if 'before' in a and 'after' in a:
                raise ValueError('Use before or after, not both')
            for key in ['before', 'after']:
                if key in a and (type(a[key]) is not int or a[key] < 0):
                    raise ValueError('Cursor must be a nonnegative integer')
            forward = 'after' in a
            op, order = ('>', 'ASC') if forward else ('<', 'DESC')
            cursor = a.get('after', 0) if forward else a.get('before', 9223372036854775807)
            rows = [dict(r) for r in db.execute(f'SELECT m.*,u.name AS author_name FROM project_messages m JOIN users u ON u.id=m.actor WHERE m.project=? AND m.id{op}? ORDER BY m.id {order} LIMIT ?', (project, cursor, limit+1))]
            more = len(rows) > limit
            rows = rows[:limit]
            if not forward:
                rows.reverse()
            return {'items': rows, 'has_more': more, 'oldest': rows[0]['id'] if rows else None, 'latest': rows[-1]['id'] if rows else a.get('after', 0)}
        if action in ['project_settings','file_locks','session_resume']:
            return coord.operation(db,action,a,project_row,actor_user,activity,now)
        if action=='project_metrics':
            return telemetry.metrics(db,project,a.get('days',30),a.get('include_series',False))
        if action=='git_recovery_next':
            done={r['id'] for r in db.execute("SELECT id FROM tasks WHERE project=? AND status='Done'",(project,))}
            rows=db.execute("SELECT id,dependencies,blocked_reason FROM tasks WHERE project=? AND category='Git recovery' AND status='TODO' AND lock_until<=? AND NOT EXISTS (SELECT 1 FROM tasks active WHERE active.project=tasks.project AND active.status='In Progress' AND active.lock_until>?) ORDER BY id",(project,time.time(),time.time())).fetchall()
            return next(({'id':row['id']} for row in rows if not row['blocked_reason'].strip() and all(dep in done for dep in json.loads(row['dependencies']))),None)
        if action=='git_reconcile':
            import git_completion
            return git_completion.reconcile(db,a,project_row,prefix,activity,now,resolve_dependents)
        if action=='git_finalize':
            import git_completion
            return git_completion.finalize(db,a,project_row,prefix,activity,now,resolve_dependents)
        if action=='git_note':  # internal: record a post-transaction git outcome
            activity(db,a['id'],a.get('actor'),'comment',a['text'])
            return {'saved':True}
        if action=='git_cleanup_pending':  # native-only, no model context
            rows=[dict(r) for r in db.execute('SELECT id,uid,number,title,context_group,dependencies,status FROM tasks WHERE project=?',(project,))]
            return git_worktrees.related_pending(project_row['workspace_root'],a['branch'],rows,coord.settings(project_row),prefix)
        if action=='task_changes':
            row=db.execute('SELECT * FROM tasks WHERE id=? AND project=?',(a['id'],project)).fetchone()
            if not row:
                raise ValueError('Task not found')
            root=db.execute('SELECT workspace_root FROM projects WHERE id=?',(project,)).fetchone()[0]
            if not root:
                raise ValueError('Project has no workspace folder; open the project folder first')
            root=git_worktrees.workspace(root,git_worktrees.identity(row))
            subset=a.get('files')
            if subset is not None:
                subset=coord.file_plan(subset)
            import git_changes
            planned=json.loads(row['files'] or '[]')
            view=git_changes.review(root,planned,a.get('max_lines',200),include_diff=a.get('diff',True),scope=subset)
            if not view:
                raise ValueError('Could not inspect the checkout (git unavailable or timed out)')
            view['id']=a['id'];view['planned']=planned
            return view
        if action=='task_timing':
            if not db.execute('SELECT 1 FROM tasks WHERE id=? AND project=?',(a['id'],project)).fetchone():
                raise ValueError('Task not found')
            return telemetry.timing(db,a['id'],a.get('history',False),a.get('before'),a.get('limit',20))
        if action in ['task_get', 'task_activity']:
            if action == 'task_activity' and 'text' in a:
                db.execute('BEGIN IMMEDIATE')
            row = None
            if 'id' in a:
                row = db.execute('SELECT * FROM tasks WHERE id=? AND project=?', (a['id'], project)).fetchone()
            elif action == 'task_get':
                code = a.get('code', '')
                if isinstance(code, str) and code.startswith(prefix + '-') and code[len(prefix)+1:].isdigit():
                    row = db.execute('SELECT * FROM tasks WHERE project=? AND number=?', (project, int(code[len(prefix)+1:]))).fetchone()
            if not row:
                raise ValueError('Task not found; supply id or a code within this project')
            if action == 'task_get':
                import context_tools
                task=context_tools.reminder_hint(db,project,task_dict(row,prefix,a.get('include_cost',False)))
                if a.get('full'):
                    import context_batches
                    group=context_batches.detail(db,project,row['id'],prefix)
                    if group:task['context_batch']=group;task['version']=context_tools.digest(task)
                if not a.get('full'):task=context_tools.agent_task(task)
                if a.get('notes_content') and not a.get('include_notes'):raise ValueError('notes_content requires include_notes')
                if a.get('include_notes'):
                    task['notes']=context_tools.linked_notes(db,project,row['id'],a)
                    task['version']=context_tools.digest({'task':task['version'],'notes':task['notes']})
                return context_tools.conditional(task,a.get('version_seen'))
            if 'text' in a:
                actor = actor_user(db, a)
                kind = a.get('kind', 'work')
                if kind not in ['work', 'comment']:
                    raise ValueError('kind must be work or comment')
                if kind == 'work':
                    lock_access(row, a)
                    if row['status'] == 'In Progress' and row['lock_until'] <= time.time():
                        raise ValueError('Claim this task before recording work')
                entry = activity(db, row['id'], actor['id'], kind, required(a['text'], 'text', 2000))
                if kind == 'work' and row['lock_until'] > time.time():
                    until=time.time()+3600
                    db.execute('UPDATE tasks SET lock_until=?,updated=? WHERE id=?', (until, now(), row['id']))
                    coord.renew_files(db,row['id'],a['token'],until)
                return {'id': entry, 'saved': True}
            limit = a.get('limit', 20)
            if type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError('limit must be 1..50')
            if 'after' in a and 'before' in a:
                raise ValueError('Use after or before, not both')
            condition, cursor = ('id>?', a['after']) if 'after' in a else ('id<?', a.get('before', 9223372036854775807))
            order = 'ASC' if 'after' in a else 'DESC'
            entries = [dict(r) for r in db.execute(f'SELECT * FROM task_activity WHERE task=? AND {condition} ORDER BY id {order} LIMIT ?', (row['id'], cursor, limit+1))]
            more = len(entries) > limit
            entries = entries[:limit]
            if order == 'DESC':
                entries.reverse()
            return {'items': entries, 'has_more': more, 'oldest': entries[0]['id'] if entries else None, 'latest': entries[-1]['id'] if entries else a.get('after', 0)}
        if action in ['task_claim', 'task_release']:
            db.execute('BEGIN IMMEDIATE')
            actor = actor_user(db, a)
            if action=='task_claim':
                if a.get('require_light') and coord.workflow_mode(db,project,a['id'])['effective']!='Light':
                    raise ValueError('Workflow changed; use Standard coordination')
                if a.get('require_light'):
                    if coord.active_count(db,project,actor['id'],a['id']):
                        raise ValueError('Light permits one active task; finish or hand off existing work')
                    if len(a.get('files',[]))>5 and db.execute('SELECT scope_size FROM tasks WHERE id=? AND project=?',(a['id'],project)).fetchone()[0]!='Small':
                        raise ValueError('File plan exceeds lightweight scope bounds')
                    if db.execute('SELECT 1 FROM file_locks WHERE project=? AND owner_id=? AND lock_until>?',(project,actor['id'],time.time())).fetchone():
                        raise ValueError('Review existing own file reservations before claiming')
                runtime.gate(db,project,actor['id'])
            row = db.execute('SELECT * FROM tasks WHERE id=? AND project=?', (a['id'], project)).fetchone()
            if not row:
                raise ValueError('Task not found')
            if action == 'task_release':
                import context_batches
                batch=context_batches.reservation(db,project,row['id'])
                if a.get('force'):
                    if actor['kind'] != 'human':
                        raise ValueError('Only a human can force release a lock')
                else:
                    lock_access(row, a)
                    if batch and batch['actor']!=actor['id']:raise ValueError('Only batch owner or a human force release can release this reservation')
                if batch:db.execute('DELETE FROM worker_launch_offers WHERE project=? AND actor=?',(project,batch['actor']))
                db.execute('UPDATE tasks SET lock_token=NULL,lock_until=0,updated=? WHERE id=?', (now(), row['id']))
                db.execute('DELETE FROM file_locks WHERE task=?',(row['id'],))
                activity(db, row['id'], actor['id'], 'lock', 'Lock force-released by human.' if a.get('force') else 'Lock released.')
                return {'id': row['id'], 'released': True}
            lock_access(row, a)
            import context_batches
            context_batches.check_claim(db,project,row['id'],actor['id'])
            if row['status'] in ['Done', 'In Review','Cancelled']:
                raise ValueError('Reopen the task before claiming it')
            if row['blocked_reason'].strip():
                raise ValueError('Resolve the blocker before claiming')
            for dep in json.loads(row['dependencies']):
                if db.execute('SELECT status FROM tasks WHERE id=?', (dep,)).fetchone()['status'] != 'Done':
                    raise ValueError('Finish dependencies before claiming')
            coord.check_capacity(db,project,actor['id'],row['id'])
            plan=coord.file_plan(a.get('files',json.loads(row['files'])))
            root=db.execute('SELECT workspace_root FROM projects WHERE id=?',(project,)).fetchone()[0]
            paths=coord.canonical_paths(root,plan)
            held=coord.conflicts(db,paths,row['id'],actor['id'])
            if held:
                return {'claimed':False,'conflicts':held[:20],'conflicts_total':len(held)}
            seconds = a.get('lease_seconds', 3600)
            if type(seconds) is not int or not 60 <= seconds <= 14400:raise ValueError('lease_seconds must be 60..14400')
            git=git_claim(db,project,row,prefix,actor['id']) if row['lock_until'] <= time.time() else None
            if git and git.get('error'):raise ValueError('Git branch preparation failed: '+git['error'])
            if git and git.get('busy'):
                return {'claimed':False,'branch_busy':git['busy'],'hint':'Checkout holds uncommitted work of another branch; finish or hand off that work, or pick other work with exclude_ids.'}
            token = row['lock_token'] if row['lock_until'] > time.time() else secrets.token_urlsafe(24)
            until = time.time() + seconds
            db.execute("UPDATE tasks SET owner_id=?,lock_token=?,lock_until=?,status='In Progress',updated=? WHERE id=?", (actor['id'], token, until, now(), row['id']))
            db.execute('UPDATE tasks SET files=? WHERE id=?',(json.dumps(plan),row['id']))
            coord.renew_files(db,row['id'],token,until)
            coord.reserve(db,paths,project,row['id'],actor['id'],token,until,now())
            telemetry.transition(db,row['id'],'In Progress',actor['id'])
            if row['lock_until'] <= time.time():
                activity(db, row['id'], actor['id'], 'claim', 'Claimed task; status In Progress.')
            claimed={'claimed':True,'id': row['id'], 'code': f"{prefix}-{row['number']}", 'owner_id': actor['id'], 'token': token, 'lock_until': until, 'status': 'In Progress','files_reserved':len(paths)}
            if row['lock_until'] <= time.time():
                # Fresh claim: tell the worker which planned files were already edited on
                # disk (not its work) so it reviews them before building on them.
                import git_changes
                baseline=git_changes.dirty(git_worktrees.workspace(root,git_worktrees.identity(db.execute('SELECT * FROM tasks WHERE id=?',(row['id'],)).fetchone())),plan)
                if baseline:claimed['dirty_planned']=baseline[:20]
                if git:claimed['git']=git
            return claimed
        if action == 'snapshot':
            return {'runtime':runtime.summary(db,project),'settings':coord.settings(project_row),'users': [dict(r) for r in db.execute('SELECT * FROM users ORDER BY name,id')], 'tasks': [task_dict(r, prefix) for r in db.execute('SELECT * FROM tasks WHERE project=? ORDER BY priority,id', (project,))], 'notes': [dict(r, items=json.loads(r['items'])) for r in db.execute('SELECT * FROM notes WHERE project=? ORDER BY id DESC', (project,))]}
        if action == 'task_plan':
            import planning
            return planning.apply(db,a,project)
        if action == 'task_create':
            title = required(a.get('title'), 'title', 240)
            scope = required(a.get('description'),'description',12000)
            criteria = acceptance_criteria(a.get('acceptance_criteria'))
            coord.validate_scope(a.get('scope_size','Unknown'),a.get('scope_reason',''))
            db.execute('BEGIN IMMEDIATE')
            same,differs=duplicate_task(db,project,title,a)
            if same:
                return {'id':same['id'],'code':f"{prefix}-{same['number']}",'status':same['status'],'existing':True,**({'differs':differs} if differs else {}),'note':existing_note(differs)}
            number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM tasks WHERE project=?', (project,)).fetchone()[0]
            cur = db.execute('INSERT INTO tasks(project,title,description,priority,updated,checklist,number,category,acceptance_criteria) VALUES(?,?,?,?,?,?,?,?,?)', (project, title, scope, priority(a.get('priority', 2)), now(), json.dumps(checklist(a.get('checklist', []))), number, required(a.get('category','General'), 'category', 80),json.dumps(criteria)))
            import task_cost
            cost=task_cost.update(task_cost.empty(),a,actor_user(db,a)['identity'] if any(k in a for k in ('estimated_tokens','estimated_quota_percent','usage_report')) else '')
            db.execute('UPDATE tasks SET cost_data=? WHERE id=?',(json.dumps(cost),cur.lastrowid))
            deps = dependencies(db, project, cur.lastrowid, a.get('dependencies', []))
            db.execute('UPDATE tasks SET scope_size=?,scope_reason=? WHERE id=?',(a.get('scope_size','Unknown'),a.get('scope_reason',''),cur.lastrowid))
            db.execute('UPDATE tasks SET dependencies=? WHERE id=?', (json.dumps(deps), cur.lastrowid))
            db.execute('UPDATE tasks SET files=? WHERE id=?',(json.dumps(coord.file_plan(a.get('files',[]))),cur.lastrowid))
            import model_routing
            model_routing.validate(a.get('model',''),a.get('model_tier','auto'),a.get('model_reason',''))
            if a.get('model') and actor_user(db,a)['kind']!='human':raise ValueError('Only a human can set a fixed task model')
            db.execute('UPDATE tasks SET model=?,model_tier=?,model_reason=? WHERE id=?',(a.get('model',''),a.get('model_tier','auto'),a.get('model_reason',''),cur.lastrowid))
            validate_verification(a.get('verification_level','auto'),a.get('verification_reason',''))
            db.execute('UPDATE tasks SET verification_level=?,verification_reason=? WHERE id=?',(a.get('verification_level','auto'),a.get('verification_reason',''),cur.lastrowid))
            db.execute('UPDATE tasks SET context_group=? WHERE id=?',(validate_context_group(a.get('context_group','')),cur.lastrowid))
            telemetry.transition(db,cur.lastrowid,'TODO' ,a.get('actor'),source='created')
            activity(db, cur.lastrowid, a.get('actor'), 'created', 'Task created in TODO.')
            if a.get('log'):
                activity(db, cur.lastrowid, actor_user(db,a)['id'], 'work', required(a['log'], 'log', 2000))
            return {'id': cur.lastrowid, 'code': f'{prefix}-{number}', 'status': 'TODO'}
        if action == 'task_update':
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM tasks WHERE id=? AND project=?', (int(a['id']), project)).fetchone()
            if not old:
                raise ValueError('Task not found')
            routing_only=set(a)-{'project','id','actor','token','measurement_session'} <= {'model'} and 'model' in a
            if not (routing_only and actor_user(db,a)['kind']=='human'):lock_access(old, a)
            if routing_only:
                from model_routing import validate
                if actor_user(db,a)['kind']!='human':raise ValueError('Only a human can set a fixed task model')
                validate(a['model'])
                db.execute('UPDATE tasks SET model=? WHERE id=?',(a['model'],old['id']))
                activity(db,old['id'],a['actor'],'comment','Human task model: '+(a['model'] or 'Automatic')+'; applies on next launch.')
                return {'id':old['id'],'code':f"{prefix}-{old['number']}",'status':old['status'],'model':a['model']}
            if a.get('claim') or a.get('status')=='In Progress' or (old['status']=='In Progress' and 'status' not in a):
                runtime.gate(db,project,a.get('actor'))
            claiming = a.get('claim', False)
            if type(claiming) is not bool:
                raise ValueError('claim must be boolean')
            if claiming:
                actor_user(db, a)
                import context_batches
                context_batches.check_claim(db,project,old['id'],a['actor'])
                if a.get('status', 'In Progress') != 'In Progress':
                    raise ValueError('Claim requires In Progress')
                if old['status'] in ['Done', 'In Review','Cancelled']:
                    raise ValueError('Reopen the task before claiming')
            recovering = old['lock_until'] <= time.time() and a.get('status') in ['TODO','Blocked','Cancelled'] and a.get('actor') is not None and actor_user(db,a)['kind'] == 'human'
            if old['status'] == 'In Progress' and old['lock_until'] <= time.time() and not claiming and not recovering:
                raise ValueError('Work lock expired or released; claim again before editing')
            if 'context_group' in a:
                actor_user(db,a)
                db.execute('UPDATE tasks SET context_group=? WHERE id=?',(validate_context_group(a['context_group']),old['id']))
            t = task_dict(old, prefix)
            import task_cost
            if any(k in a for k in ('estimated_tokens','estimated_quota_percent','usage_report')):
                cost=task_cost.update(json.loads(old['cost_data']),a,actor_user(db,a)['identity'])
                db.execute('UPDATE tasks SET cost_data=? WHERE id=?',(json.dumps(cost),t['id']))
            import model_routing
            routing=model_routing.request(t)
            for key in routing:
                if key in a:routing[key]=a[key]
            model_routing.validate(routing['model'],routing['model_tier'],routing['model_reason'])
            if 'model' in a and a['model']!=old['model'] and actor_user(db,a)['kind']!='human':raise ValueError('Only a human can change a fixed task model')
            if any(key in a for key in ('model_tier','model_reason')):
                actor_user(db,a)
                if old['model'] and actor_user(db,a)['kind']!='human':raise ValueError('Human task model is fixed; automatic routing cannot replace it')
            db.execute('UPDATE tasks SET model=?,model_tier=?,model_reason=? WHERE id=?',(*routing.values(),t['id']))
            level=a.get('verification_level',old['verification_level']);reason=a.get('verification_reason',old['verification_reason'])
            validate_verification(level,reason)
            db.execute('UPDATE tasks SET verification_level=?,verification_reason=? WHERE id=?',(level,reason,t['id']))
            for field in ('scope_size','scope_reason'):
                if field in a:t[field]=a[field]
            coord.validate_scope(t['scope_size'],t['scope_reason'])
            if 'continuity' in a:
                actor_user(db,a)
                if not isinstance(a['continuity'],str) or len(a['continuity'])>2000:
                    raise ValueError('continuity must be text, max 2000 characters')
                t['continuity']=a['continuity'].strip()
            for key in ['title', 'description', 'acceptance_criteria', 'status', 'priority', 'blocked_reason', 'blocked_reason_kind', 'dependencies', 'checklist', 'category', 'files','evidence','cancel_reason']:
                if key in a:
                    t[key] = a[key]
            if old['status']=='Blocked' and a.get('status')=='TODO' and actor_user(db,a)['kind']=='human':
                # Drag/drop omits the reason; the edit form echoes its old value.
                # Both are an explicit human retry, not a new unresolved blocker.
                if t['blocked_reason'].strip() and t['blocked_reason'].strip()!=old['blocked_reason'].strip():
                    raise ValueError('Use Blocked to save a new blocker, or clear it to retry in TODO')
                t['blocked_reason']=''
                t['blocked_reason_kind']='external'
                activity(db,t['id'],a['actor'],'comment','Human requested retry in TODO; previous blocker cleared: '+old['blocked_reason'][:1800])
            if claiming:
                t['status'] = 'In Progress'
            required(t['title'], 'title', 240)
            description(t['description'])
            if 'description' in a:
                t['description']=required(a['description'],'description',12000)
            t['acceptance_criteria']=acceptance_criteria(t['acceptance_criteria'],allow_empty='acceptance_criteria' not in a)
            description(t['blocked_reason'])
            if t['blocked_reason_kind'] not in ('dependency','external','checkout'):
                raise ValueError('blocked_reason_kind must be dependency, external or checkout')
            if t['blocked_reason_kind']=='checkout' and t['category']!='Git recovery':
                raise ValueError('checkout blockers are limited to Git recovery tasks')
            if t['blocked_reason'].strip() and t['dependencies'] and a.get('blocked_reason')!=old['blocked_reason'] and 'blocked_reason' in a and 'blocked_reason_kind' not in a:
                raise ValueError('With dependencies, specify blocked_reason_kind=dependency for dependency-only prose or external for a separate constraint; omit prose when task links suffice')
            priority(t['priority'])
            t['category'] = required(t['category'], 'category', 80)
            t['checklist'] = checklist(t['checklist'])
            t['files'] = coord.file_plan(t['files'])
            if not isinstance(t['evidence'],str) or len(t['evidence'])>4000:
                raise ValueError('evidence must be text, max 4000 characters')
            if old['status']=='Done' and t['status']!='Done' and 'evidence' not in a:
                t['evidence']=''
            description(t['cancel_reason'])
            if old['status']=='Cancelled' and t['status']!='Cancelled' and 'cancel_reason' not in a:
                t['cancel_reason']=''
            owner = a.get('owner_id', old['owner_id'])
            if claiming:
                if 'owner_id' in a and a['owner_id'] != a['actor']:
                    raise ValueError('Claimed work must belong to the claiming actor')
                owner = a['actor']
            if 'owner_id' in a:
                actor_user(db, a)
                if old['lock_until'] > time.time() and owner != old['owner_id']:
                    raise ValueError('Release the lock before reassigning')
                if owner == 0:
                    owner = None
                elif not db.execute('SELECT id FROM users WHERE id=? AND active=1', (owner,)).fetchone():
                    raise ValueError('Owner not found')
            if t['status'] == 'In Progress' and ('status' in a or old['status'] != 'In Progress') and old['lock_until'] <= time.time() and not claiming:
                raise ValueError('Use task_claim to start work and acquire a lock')
            if t['status'] not in STATUSES:
                raise ValueError('Invalid status')
            if t['status']=='Cancelled':
                required(t['cancel_reason'],'cancel_reason',2000)
                if old['status']!='Cancelled':
                    actor_user(db,a)
            if t['status'] == 'Done' and old['status'] != 'Done':
                actor_user(db, a)
                policy=db.execute('SELECT require_evidence FROM projects WHERE id=?',(project,)).fetchone()[0]
                if policy and (not a.get('evidence','').strip() or any(not item['done'] for item in t['checklist'])):
                    raise ValueError('Done requires fresh verification evidence and a completed checklist')
            if t['status'] == 'Blocked' and not t['blocked_reason'].strip() and not t['dependencies']:
                raise ValueError('Blocked tasks need a reason or dependency')
            if claiming and t['blocked_reason'].strip():
                raise ValueError('Resolve the blocker before claiming')
            t['dependencies'] = dependencies(db, project, t['id'], t['dependencies'])
            if t['blocked_reason_kind']=='dependency':
                if not t['dependencies'] and (a.get('blocked_reason_kind')=='dependency' or a.get('blocked_reason','').strip()):
                    raise ValueError('A dependency-linked reason needs task dependencies')
                pending=any(db.execute('SELECT status FROM tasks WHERE id=?',(dep,)).fetchone()[0] not in ('Done','Cancelled') for dep in t['dependencies'])
                if not pending:
                    t['blocked_reason']=''
                    if t['status']=='Blocked':t['status']='TODO'
            if not t['blocked_reason'].strip():t['blocked_reason_kind']='external'
            if t['status'] in ['In Progress', 'In Review', 'Done']:
                for dep in t['dependencies']:
                    if db.execute('SELECT status FROM tasks WHERE id=?', (dep,)).fetchone()['status'] != 'Done':
                        raise ValueError('Finish dependencies before progressing this task')
            import git_sync
            completion_requested=t['status']=='Done' and old['status']!='Done'
            strict_completion=completion_requested and t['category']!='Git recovery' and git_sync.integrated_completion(coord.settings(project_row))
            if strict_completion:
                # Persist verification before Git, but do not unblock dependents yet.
                t['status']='In Review'
            paths=[]
            if claiming:
                coord.check_capacity(db,project,a['actor'],t['id'])
                root=db.execute('SELECT workspace_root FROM projects WHERE id=?',(project,)).fetchone()[0]
                paths=coord.canonical_paths(root,t['files'])
                held=coord.conflicts(db,paths,t['id'],a['actor'])
                if held:
                    return {'claimed':False,'conflicts':held[:20],'conflicts_total':len(held)}
                git=git_claim(db,project,old,prefix,a['actor']) if old['lock_until'] <= time.time() else None
                if git and git.get('error'):raise ValueError('Git branch preparation failed: '+git['error'])
                if git and git.get('busy'):
                    return {'claimed':False,'branch_busy':git['busy'],'hint':'Checkout holds uncommitted work of another branch; finish or hand off that work, or pick other work with exclude_ids.'}
            db.execute('UPDATE tasks SET title=?,description=?,status=?,priority=?,blocked_reason=?,dependencies=?,checklist=?,updated=? WHERE id=? AND project=?', (t['title'], t['description'], t['status'], int(t['priority']), t['blocked_reason'], json.dumps(t['dependencies']), json.dumps(t['checklist']), now(), t['id'], project))
            db.execute('UPDATE tasks SET blocked_reason_kind=? WHERE id=?',(t['blocked_reason_kind'],t['id']))
            until = old['lock_until']
            token = old['lock_token']
            if claiming:
                token = old['lock_token'] if old['lock_until'] > time.time() else secrets.token_urlsafe(24)
                until = time.time()+3600
            elif 'status' in a and t['status'] != 'In Progress':
                until, token = 0, None
            elif until > time.time():
                until = time.time() + 3600
            db.execute('UPDATE tasks SET owner_id=?,lock_token=?,lock_until=? WHERE id=?', (owner, token, until, t['id']))
            db.execute('UPDATE tasks SET category=? WHERE id=?', (t['category'], t['id']))
            db.execute('UPDATE tasks SET acceptance_criteria=? WHERE id=?',(json.dumps(t['acceptance_criteria']),t['id']))
            db.execute('UPDATE tasks SET files=?,evidence=? WHERE id=?',(json.dumps(t['files']),t['evidence'],t['id']))
            db.execute('UPDATE tasks SET cancel_reason=? WHERE id=?',(t['cancel_reason'],t['id']))
            db.execute('UPDATE tasks SET scope_size=?,scope_reason=? WHERE id=?',(t['scope_size'],t['scope_reason'],t['id']))
            if any(t[k]!=old[k] for k in ('scope_size','scope_reason')):
                activity(db,t['id'],actor_user(db,a)['id'],'work','Scope estimate: '+t['scope_size']+'. '+t['scope_reason'])
            if 'continuity' in a:
                import context_tools
                db.execute('UPDATE tasks SET continuity=?,continuity_basis=? WHERE id=?',(t['continuity'],context_tools.basis(t),t['id']))
                activity(db,t['id'],a['actor'],'work','Continuity summary updated: '+t['continuity'] if t['continuity'] else 'Continuity summary cleared.')
            state_change=telemetry.transition(db,t['id'],t['status'],a.get('actor'))
            if until>time.time():
                coord.renew_files(db,t['id'],token,until)
                if claiming:
                    coord.reserve(db,paths,project,t['id'],owner,token,until,now())
            else:
                db.execute('DELETE FROM file_locks WHERE task=?',(t['id'],))
            previous=task_dict(old,prefix)
            changes = {key: t[key] for key in ['title','description','acceptance_criteria','status','priority','blocked_reason','blocked_reason_kind','dependencies','checklist','category','files','evidence','cancel_reason'] if t[key] != previous[key]}
            if owner != old['owner_id']:
                changes['owner_id'] = owner
            if changes:
                # Keep large descriptions/checklists out of automatic history payloads.
                summary = '; '.join(f'{key}: {value}' if key in ['status','priority','category','owner_id','dependencies'] else f'{key} updated' for key,value in changes.items())
                if state_change:
                    summary+=f"; time in {state_change['from']}: {state_change['seconds']} calendar seconds"
                activity(db, t['id'], a.get('actor'), 'updated', summary)
            if claiming and old['lock_until'] <= time.time():
                activity(db, t['id'], a['actor'], 'claim', 'Claimed task; status In Progress.')
            if completion_requested and t['evidence'].strip():
                activity(db,t['id'],a['actor'],'verification',t['evidence'].strip())
            if t['status']=='Cancelled' and old['status']!='Cancelled':
                activity(db,t['id'],a['actor'],'comment','Cancelled: '+t['cancel_reason'])
            if a.get('log'):
                activity(db, t['id'], actor_user(db,a)['id'], 'work', required(a['log'], 'log', 2000))
            unblocked = resolve_dependents(db, project, t['id'], prefix, a.get('actor')) if t['status'] in ['Done','Cancelled'] and old['status'] != t['status'] else []
            result = {'id': t['id'], 'code': t['code'], 'status': t['status'], 'unblocked': unblocked[:20], 'unblocked_total': len(unblocked)}
            if claiming:
                result.update(claimed=True,token=token, lock_until=until, owner_id=owner,files_reserved=len(paths))
                if git:result['git']=git
            if completion_requested:
                git=git_done(db,project,dict(t,context_group=a.get('context_group',old['context_group'])),prefix,a.get('actor') if a.get('actor') is not None else owner)
                if git:result['git']=git
                if strict_completion:result['integration_pending']=True
            return result
        if action=='cli_usage_summary':
            import execution_usage
            return execution_usage.summary(db,project)
        if action=='batch_next':
            actor=actor_user(db,a)
            db.execute('BEGIN IMMEDIATE')
            import context_batches
            return context_batches.advance(db,project,actor['id'],a['id'],project_row,prefix)
        if action == 'note_cleanup':
            if actor_user(db,a)['kind']!='human':raise ValueError('Only an active human can clean legacy notes')
            db.execute('BEGIN IMMEDIATE')
            obsolete="project=? AND audience='human' AND (kind IN ('Daily','Retrospective') OR (kind='Review' AND title='Agent pause handoff'))"
            rows=[dict(row) for row in db.execute('SELECT * FROM notes WHERE '+obsolete+' ORDER BY id',(project,))]
            counts={kind:sum(row['kind']==kind for row in rows) for kind in ('Daily','Retrospective','Review')}
            result={'eligible':len(rows),'by_kind':counts,'deleted':0}
            if a.get('confirm') and rows:
                main=next(row['file'] for row in db.execute('PRAGMA database_list') if row['name']=='main')
                folder=Path(main).parent/'cleanup-backups';folder.mkdir(parents=True,exist_ok=True)
                backup=folder/('legacy-notes-'+str(time.time_ns())+'.json')
                # Exclusive durable backup completes before the transactional delete.
                with backup.open('x',encoding='utf-8') as saved:
                    json.dump({'project':project,'notes':rows},saved,ensure_ascii=False)
                    saved.flush();os.fsync(saved.fileno())
                result['deleted']=db.execute('DELETE FROM notes WHERE '+obsolete,(project,)).rowcount
                result['backup']=str(backup)
            return result
        if action == 'note_delete':
            if actor_user(db,a)['kind']!='human':raise ValueError('Only an active human can delete notes')
            if type(a.get('id')) is not int:raise ValueError('Note id must be an integer')
            cur=db.execute('DELETE FROM notes WHERE project=? AND id=?',(project,a['id']))
            if not cur.rowcount:raise ValueError('Note not found in this project')
            return {'id':a['id'],'deleted':True}
        if action == 'note_add':
            kind = a['kind']
            if kind not in ['Daily', 'Retrospective', 'Review']:
                raise ValueError('Invalid note kind')
            items = a.get('items', [])
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError('items must be a list, max 100')
            items = [required(i, 'item', 4000) for i in items]
            task=a.get('task')
            if task is not None and (type(task) is not int or not db.execute('SELECT 1 FROM tasks WHERE id=? AND project=?',(task,project)).fetchone()):raise ValueError('Related task must belong to this project')
            audience=a.get('audience','human')
            if audience not in ('human','agent'):raise ValueError('Invalid note audience')
            if audience=='agent' and (task is None or kind!='Review'):raise ValueError('AI reminders require a related task and Review kind')
            cur = db.execute('INSERT INTO notes(project,kind,title,items,created,task,audience) VALUES(?,?,?,?,?,?,?)', (project, kind, required(a.get('title'), 'title', 240), json.dumps(items), now(),task,audience))
            return {'id': cur.lastrowid}
        if action == 'note_check':
            cur = db.execute('UPDATE notes SET done=? WHERE id=? AND project=? AND kind=?', (int(bool(a['done'])), int(a['id']), project, 'Review'))
            if not cur.rowcount:
                raise ValueError('Review note not found')
            return {'id': a['id'], 'done': bool(a['done'])}
        if action in ('next_work','worker_launch'):
            db.execute('CREATE TABLE IF NOT EXISTS worker_launch_offers(project INTEGER NOT NULL,actor INTEGER NOT NULL,task INTEGER NOT NULL,expires REAL NOT NULL,PRIMARY KEY(project,actor),UNIQUE(project,task))')
            if action=='worker_launch':
                if actor_user(db,a)['kind']!='agent':raise ValueError('Worker launch requires an active agent')
                mode=a.get('action','reserve')
                if mode not in ('reserve','release'):raise ValueError('action must be reserve or release')
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM worker_launch_offers WHERE expires<=?',(time.time(),))
                if mode=='release':
                    db.execute('DELETE FROM worker_launch_offers WHERE project=? AND actor=?',(project,a['actor']))
                    import context_batches
                    return {'released':True,**context_batches.release(db,project,a['actor'])}
            stop=runtime.reason(db,project,a.get('actor')) if a.get('actor') is not None or runtime.control(db,project)['paused'] else ''
            if stop:
                return {'task':None,'should_stop':True,'reason':stop,'settings':coord.settings(project_row) if a.get('settings_seen')!=project_row['settings_version'] else None,'instruction':'Stop editing; call agent_checkpoint at a safe boundary to save handoff and release own work.'}
            import context_tools
            if a.get('actor') is not None and not coord.parallel_available(db,project,a['actor']):
                if action=='worker_launch':return {'eligible':False,'reason':'Project parallel-agent limit reached; no model was started.'}
                return {'task':None,'reason':'Project parallel-agent limit reached','capacity':{'parallel_available':False,'parallel_limit':project_row['max_parallel_agents']}}
            if action=='worker_launch':
                reconcile_checkout_blocks(db,project_row,a['actor'])
            tasks = [context_tools.reminder_hint(db,project,task_dict(r, prefix)) for r in db.execute('SELECT * FROM tasks WHERE project=? ORDER BY priority,id', (project,))]
            done = {t['id'] for t in tasks if t['status'] == 'Done'}
            tasks = [t for t in tasks if not t['locked'] or t['owner_id'] == a.get('actor')]
            ready = lambda t: not t['blocked_reason'].strip() and all(i in done for i in t['dependencies'])
            blocked=[{'id':t['id'],'code':t['code'],'title':t['title'],'status':t['status'],'dependencies':t['dependencies'],'availability':'blocked_by_reason' if t['blocked_reason'].strip() else 'waiting_for_dependencies'} for t in tasks if t['status']=='Blocked']
            # A prose blocker requires an agent decision; never silently clear it.
            candidates = [t for t in tasks if t['status'] == 'In Progress' and ready(t)]
            candidates += [t for t in tasks if t['status'] == 'Blocked' and t['dependencies'] and ready(t) and not t['blocked_reason']]
            # Own paused work (handoff continuity) resumes before fresh TODO work.
            mine=lambda t: a.get('actor') is not None and t['owner_id']==a.get('actor') and bool(t.get('continuity'))
            candidates += [t for t in tasks if t['status'] == 'TODO' and ready(t) and mine(t)]
            candidates += [t for t in tasks if t['status'] == 'TODO' and ready(t) and not mine(t)]
            excluded=a.get('exclude_ids',[])
            if not isinstance(excluded,list) or len(excluded)>100 or any(type(i) is not int for i in excluded):
                raise ValueError('exclude_ids must be a list of at most 100 task IDs')
            candidates=[t for t in candidates if t['id'] not in excluded]
            import context_batches
            candidates=[t for t in candidates if not (held:=context_batches.reservation(db,project,t['id'])) or (held['actor']==a.get('actor') and held['current']==t['id'])]
            active=coord.active_count(db,project,a.get('actor')) if a.get('actor') is not None else 0
            limit=project_row['max_active_per_agent']
            if a.get('actor') is not None and actor_user(db,a)['kind']=='agent' and active>=limit:
                candidates=[t for t in candidates if t['owner_id']==a['actor'] and t['locked']]
            available=[]
            offers=db.execute('SELECT actor,task FROM worker_launch_offers WHERE project=? AND actor!=? AND expires>?',(project,a.get('actor',-1),time.time())).fetchall()
            offered_ids={o['task'] for o in offers}
            offered_paths=set()
            for task in tasks:
                if task['id'] in offered_ids:
                    try:offered_paths.update(coord.canonical_paths(project_row['workspace_root'],task['files']))
                    except (ValueError,OSError):pass
            conflicts_skipped=0
            missing_root=0
            invalid_files=0
            for task in candidates:
                own_resume=task['locked'] and task['owner_id']==a.get('actor')
                if task['id'] in offered_ids and not own_resume:continue
                if task['files'] and not project_row['workspace_root']:
                    missing_root+=1
                    continue
                try:
                    paths=coord.canonical_paths(project_row['workspace_root'],task['files'])
                except (ValueError,OSError):
                    invalid_files+=1
                    continue
                if (offered_paths.intersection(paths) and not own_resume) or coord.conflicts(db,paths,task['id'],a.get('actor')):
                    conflicts_skipped+=1
                else:
                    available.append(task)
            candidates=available
            # Branches mode: one checkout works one branch at a time, so never
            # suggest work whose claim would be refused with branch_busy. The
            # checkout belongs to the branch of other active tasks, or to the
            # current branch while it holds uncommitted edits.
            branch_skipped=0
            checkout_recovery=None
            settings=coord.settings(project_row)
            if settings.get('git_mode')=='Branches' and project_row['workspace_root'] and candidates:
                import git_sync
                task_branch=lambda t:git_sync.branch_for(settings,{'code':t['code'],'title':t['title'],'context_group':t.get('context_group','')})
                active_rows=db.execute("SELECT * FROM tasks WHERE project=? AND status='In Progress' AND lock_until>?",(project,time.time())).fetchall()
                branches={task_branch(task_dict(r,prefix)) for r in active_rows}
                owned=next(iter(branches)) if len(branches)==1 else None
                if owned is None and not branches:
                    try:current=git_sync.refresh_index_state(project_row['workspace_root'])
                    except (ValueError,OSError,subprocess.SubprocessError):current=git_sync.state(project_row['workspace_root'])
                    if current and current[1]:
                        owned=current[0]
                        checkout_recovery={'branch':current[0],'files':current[1][:100]}
                        # Mutate only at the supervisor's existing launch boundary,
                        # never during board reads or next_work navigation.
                        if action=='worker_launch' and settings.get('git_pause_blocked') and not any(task_branch(t)==owned and t['category']!='Git recovery' for t in candidates) and any(task_branch(t)!=owned and t['category']!='Git recovery' for t in candidates):
                            unfinished=[t for t in tasks if task_branch(t)==owned and t['status']=='Blocked' and t['owner_id']==a.get('actor')]
                            safe=bool(unfinished)
                            reserved=any(Path(lock['path']).is_relative_to(Path(project_row['workspace_root']).resolve()) for lock in db.execute('SELECT path FROM file_locks WHERE lock_until>?',(time.time(),)))
                            if safe and not reserved:
                                import git_paused
                                planned=[]
                                for t in unfinished:planned.extend(git_worktrees.files(project_row['workspace_root'],t['files']))
                                try:
                                    with git_sync.checkout_lock(project_row['workspace_root']):
                                        groups={t.get('context_group','').strip() for t in unfinished}
                                        group=next(iter(groups)) if len(groups)==1 else ''
                                        paused=git_paused.pause(project_row['workspace_root'],owned,planned,[t['code'] for t in unfinished],context_group=group)
                                    if paused:
                                        for t in unfinished:activity(db,t['id'],a['actor'],'comment','git: unfinished edits preserved in a private local checkpoint; restored automatically on this branch before resuming. Not merged or pushed.')
                                        owned=None;checkout_recovery=None
                                except (ValueError,OSError,subprocess.SubprocessError) as error:
                                    # Preservation errors keep the branch busy. Its
                                    # durable manifest prevents destructive retries.
                                    checkout_recovery['error']='Local checkpoint needs reconciliation: '+str(error)[:300]+'. Inspect the saved manifest in .scrum/local/git-paused/ before repeating work.'
                if owned is not None or branches:
                    # Recovery deliberately uses the existing checkout (including
                    # dirty staging), but cannot interrupt another live claim.
                    keep=[t for t in candidates if (t['locked'] and t['owner_id']==a.get('actor')) or
                          (not active_rows if t['category']=='Git recovery' else task_branch(t)==owned)]
                    branch_skipped=len(candidates)-len(keep)
                    candidates=keep
            if action=='worker_launch' and a.get('id') is not None:candidates=[t for t in candidates if t['id']==a['id']]
            if action=='worker_launch':
                if not candidates:
                    reason=(str(branch_skipped)+' ready task(s) need another branch, but this checkout is owned by active work or contains uncommitted edits. Preserve those edits; finish/recover this branch or use Worktrees for isolation.' if branch_skipped else 'No eligible unreserved work; no model was started.')
                    return {'eligible':False,'reason':reason,**({'checkout_recovery':checkout_recovery} if checkout_recovery and branch_skipped and settings.get('agent_followup_tasks') else {})}
                selected=candidates[0]
                if selected['locked'] and selected['owner_id']==a['actor']:
                    db.execute('DELETE FROM worker_launch_offers WHERE project=? AND task=? AND actor!=?',(project,selected['id'],a['actor']))
                selection={}
                if a.get('client'):
                    import model_routing
                    selection=model_routing.select(selected,a['client'],model_routing.profile_model(project_row['workspace_root'],a['actor']))
                    db.execute('UPDATE tasks SET model_selection=? WHERE id=?',(json.dumps(selection),selected['id']))
                    if selected.get('model_selection')!=selection:activity(db,selected['id'],a['actor'],'comment','Model selected: '+(selection['model'] or 'Provider default')+' ('+selection['source']+'): '+selection['reason'])
                db.execute('INSERT INTO worker_launch_offers(project,actor,task,expires) VALUES(?,?,?,?) ON CONFLICT(project,actor) DO UPDATE SET task=excluded.task,expires=excluded.expires',(project,a['actor'],selected['id'],time.time()+600))
                batch=[]
                if selection:
                    import context_batches
                    selected['model_selection']=selection
                    batch=context_batches.plan(db,project,a['actor'],selected,tasks,selection,project_row['workspace_root'])
                return {'eligible':True,'id':selected['id'],'code':selected['code'],'expires_in_seconds':600,'ownership_granted':False,**({'selection':selection,'batch':batch} if selection else {})}
            if candidates:candidates[0]=context_tools.selection_header(candidates[0])
            if candidates and a.get('task_seen'):
                import context_tools
                candidates[0]=context_tools.conditional(candidates[0],a['task_seen'])
            import context_tools
            return {'settings':coord.settings(project_row) if a.get('settings_seen')!=project_row['settings_version'] else None,'capacity':{'active':active,'limit':limit},'file_conflicts_skipped':conflicts_skipped,'missing_workspace_skipped':missing_root,'invalid_files_skipped':invalid_files,**({'branch_skipped':branch_skipped} if branch_skipped else {}),'task': candidates[0] if candidates else None, 'blocked': blocked[:20], 'blocked_total': len(blocked), 'instruction': 'Resume In Progress, then actionable Blocked, then TODO. Claim task and planned files before edits; on conflict choose other work. No candidate can mean locked work or capacity/configuration limits, not an empty backlog. Never invent busywork.'}
        raise ValueError('Unknown action')
