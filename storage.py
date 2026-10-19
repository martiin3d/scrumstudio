"""Folder-scoped project databases, local registry and portable JSON snapshots.

Registry IDs and actor IDs are machine-local. Snapshot relationships use stable
UUIDs/identity strings, never those local IDs. Attached SQLite databases keep
task/file claims transactional on one checkout.
"""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
import shutil
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
VERSION = 1
TABLES = ('tasks', 'notes', 'task_activity', 'task_states', 'project_messages')
PORTABLE_ACTIONS = {'project_open', 'project_export', 'project_forget', 'project_restore', 'project_migrate'}


def registry_path():
    return Path(os.environ.get('SCRUM_REGISTRY', ROOT / 'data' / 'registry.db'))


@contextmanager
def registry():
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=20)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL, path TEXT NOT NULL UNIQUE, checkout TEXT NOT NULL)')
        yield db


def atomic_json(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
            json.dump(value, out, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            out.write('\n')
        if exclusive:
            gate = path.with_name(path.name+'.creating')
            for attempt in range(100):
                try:
                    gate.mkdir()
                    break
                except FileExistsError:
                    time.sleep(.02)
            else:
                raise ValueError('Project metadata creation is busy; retry after the other operation finishes')
            try:
                if path.exists():
                    return False
                os.replace(temporary, path)
            finally:
                gate.rmdir()
        else:
            os.replace(temporary, path)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 50_000_000:
        raise ValueError('Project JSON exceeds 50 MB')
    try:
        def reject_constant(value):
            raise ValueError('Non-finite numbers are not valid project data')
        return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'Invalid project JSON: {path.name}') from error


def valid_uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid project/entity UUID') from None


def folder_path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError('Supply an absolute project folder path')
    path = Path(value).resolve()
    if not path.is_dir():
        raise ValueError('Project folder must already exist')
    # Do not follow metadata symlinks into another checkout.
    for item in [path / '.scrum', path / '.scrum' / 'local', path / '.scrum' / '.gitignore', path / '.scrum' / 'project.json', path / '.scrum' / 'snapshot.json']:
        if item.is_symlink():
            raise ValueError('Project metadata cannot be symlinks')
    return path


def metadata(folder):
    from store import required, description
    value = read_json(folder / '.scrum' / 'project.json')
    if not isinstance(value, dict) or value.get('schema_version') != VERSION:
        raise ValueError('Unsupported project format version')
    valid_uuid(value.get('uuid'))
    required(value.get('name'),'name',120)
    description(value.get('description',''))
    return value


def local_paths(folder):
    local = folder / '.scrum' / 'local'
    local.mkdir(parents=True, exist_ok=True)
    for file in ['scrum.db', 'locks.db', 'checkout.json']:
        if (local / file).is_symlink():
            raise ValueError('Local project files cannot be symlinks')
    return local / 'scrum.db', local / 'locks.db'


def ensure_layout(folder, project_uuid):
    main, locks = local_paths(folder)
    ignore = folder / '.scrum' / '.gitignore'
    content = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    if '/local/' not in content.splitlines():
        ignore.write_text(content + ('\n' if content and not content.endswith('\n') else '') + '/local/\n', encoding='utf-8')
    checkout_file = main.parent / 'checkout.json'
    if checkout_file.exists():
        checkout = read_json(checkout_file)
        if checkout.get('project_uuid') != project_uuid:
            raise ValueError('Local checkout identity does not match project UUID')
        valid_uuid(checkout.get('uuid'))
    else:
        checkout = {'uuid': str(uuid.uuid4()), 'project_uuid': project_uuid}
        if not atomic_json(checkout_file, checkout, exclusive=True):
            checkout = read_json(checkout_file)
            if checkout.get('project_uuid') != project_uuid:
                raise ValueError('Local checkout identity does not match project UUID')
            valid_uuid(checkout.get('uuid'))
    return main, locks, checkout['uuid']


def identities(action, args, historical=False):
    from store import required
    path = registry_path().parent / 'identities.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=20)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,identity TEXT UNIQUE NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL)')
        if 'active' not in {r['name'] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        if action == 'users':
            return [dict(r) for r in db.execute('SELECT * FROM users ORDER BY id')]
        if action == 'user_manage':
            db.execute('BEGIN IMMEDIATE')
            actor=db.execute('SELECT * FROM users WHERE id=?',(args.get('actor'),)).fetchone()
            if not actor or not actor['active'] or actor['kind']!='human':
                raise ValueError('Only an active human can manage users')
            target=db.execute('SELECT * FROM users WHERE id=?',(args.get('id'),)).fetchone()
            if not target:
                raise ValueError('User not found')
            mode=args.get('action')
            if mode not in ['delete','restore']:
                raise ValueError('action must be delete or restore')
            if mode=='delete':
                check_user_deletion(db,target)
                if target['kind']=='agent':
                    def deactivate():
                        db.execute('UPDATE users SET active=0 WHERE id=?',(target['id'],))
                        db.commit() # Prevent new claims in already-recovered projects.
                    result=recover_agent(args,target,deactivate)
                    if result.get('pending'):
                        return result
                    db.execute('UPDATE users SET active=0 WHERE id=?',(target['id'],))
                    return dict(result,id=target['id'],active=False,history_preserved=True)
                # Human profiles retain the live-work guard.
                from store import connect
                with registry() as catalog:
                    folders=[dict(r) for r in catalog.execute('SELECT * FROM folders')]
                for registered in folders:
                    folder=folder_path(registered['path'])
                    main,locks=local_paths(folder)
                    with closing(connect(main,locks)) as project_db:
                        check_live_user(project_db,target['id'])
            db.execute('UPDATE users SET active=? WHERE id=?',(int(mode=='restore'),target['id']))
            return {'id':target['id'],'active':mode=='restore','history_preserved':True}
        identity = required(args.get('identity'), 'identity', 160)
        name = required(args.get('name'), 'name', 120)
        kind = args.get('kind')
        if kind not in ['human', 'agent']:
            raise ValueError('kind must be human or agent')
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM users WHERE identity=?', (identity,)).fetchone()
        if old:
            if not old['active'] and not historical:
                raise ValueError('User was deleted; a human must restore it explicitly')
            if old['kind'] != kind:
                raise ValueError('Identity already registered with a different kind')
            return dict(old)
        if 'id' in args:
            db.execute('INSERT INTO users(id,identity,name,kind) VALUES(?,?,?,?)', (args['id'],identity,name,kind))
        else:
            db.execute('INSERT INTO users(identity,name,kind) VALUES(?,?,?)', (identity,name,kind))
        return dict(db.execute('SELECT * FROM users WHERE identity=?', (identity,)).fetchone())


def sync_users(db):
    for user in identities('users', {}):
        old = db.execute('SELECT identity FROM users WHERE id=?', (user['id'],)).fetchone()
        if old and old['identity'] != user['identity']:
            raise ValueError('Local actor identity mismatch; restore a portable snapshot')
        db.execute('INSERT OR IGNORE INTO users(id,identity,name,kind) VALUES(:id,:identity,:name,:kind)', user)
        db.execute('UPDATE users SET active=? WHERE id=? AND active!=?',(user['active'],user['id'],user['active']))


def check_live_user(db, actor):
    if db.execute('SELECT 1 FROM tasks WHERE owner_id=? AND lock_until>?',(actor,time.time())).fetchone():
        raise ValueError('Release this user\'s live task claims before deleting it')
    if db.execute('SELECT 1 FROM file_locks WHERE owner_id=? AND lock_until>?',(actor,time.time())).fetchone():
        raise ValueError('Release this user\'s live file locks before deleting it')
    if db.execute('SELECT 1 FROM agent_sessions WHERE actor=? AND ((status IN (\'working\',\'stopping\') AND heartbeat>?) OR bridge_heartbeat>?)',(actor,time.time()-600,time.time()-15)).fetchone():
        raise ValueError('Stop and close this user\'s agent session before deleting it')


def recover_agent(args, target, deactivate):
    """Preflight all folders; recover each transaction idempotently on retry."""
    from store import connect, run
    import managed_agents
    with registry() as catalog:
        folders=[dict(r) for r in catalog.execute('SELECT * FROM folders ORDER BY id')]
    available=[]
    pending=[]
    # Never create a replacement database for unavailable project data.
    for registered in folders:
        try:
            folder=folder_path(registered['path'])
            main=folder/'.scrum/local/scrum.db';locks=folder/'.scrum/local/locks.db'
            if not main.is_file() or not locks.is_file():
                raise ValueError('Local project databases are missing')
            if metadata(folder)['uuid']!=registered['uuid']:
                raise ValueError('Registered project identity changed; reopen the correct folder')
            with closing(connect(main,locks)) as project_db:
                live=project_db.execute("SELECT 1 FROM agent_sessions WHERE actor=? AND ((status IN ('working','stopping') AND heartbeat>?) OR bridge_heartbeat>?)",(target['id'],time.time()-600,time.time()-15)).fetchone()
            if live:
                profile_file=folder/'.scrum/local/agent-profiles.db'
                profiles=[]
                if profile_file.is_file():
                    with closing(managed_agents.connect(folder)) as profiles_db:
                        profiles=[dict(r) for r in profiles_db.execute('SELECT * FROM profiles WHERE actor=?',(target['id'],))]
                if not profiles:
                    raise ValueError('Stop and close the active unmanaged agent session before retrying deletion')
                for profile in profiles:
                    managed_agents.operation({'project':registered['id'],'actor':args['actor'],'action':'stop','id':profile['id']},run)
                pending.append(registered['id'])
            available.append((registered,main,locks))
        except (OSError,ValueError,sqlite3.Error) as error:
            raise ValueError(f"Agent deletion cannot recover project {registered['id']}: {error}. Restore access or close the worker, then retry; earlier stop requests remain effective.") from error
    if pending:
        return {'id':target['id'],'active':True,'pending':True,'status':'stop_requested','projects':pending,'next_step':'Wait for managed workers to exit and close their sessions, then retry Delete. No task recovery has run.'}
    deactivate()
    recovered=[]
    for registered,main,locks in available:
        try:
            with closing(connect(main,locks)) as project_db,project_db:
                project_db.execute('BEGIN IMMEDIATE')
                # Recheck under the write lock; a worker may have started since preflight.
                live=project_db.execute("SELECT 1 FROM agent_sessions WHERE actor=? AND ((status IN ('working','stopping') AND heartbeat>?) OR bridge_heartbeat>?)",(target['id'],time.time()-600,time.time()-15)).fetchone()
                if live:raise ValueError('Agent became active; stop it before retrying')
                sync_users(project_db)
                result=recover_agent_tasks(project_db,target,args['actor'])
                recovered.append(dict(project=registered['id'],**result))
        except (OSError,ValueError,sqlite3.Error) as error:
            raise ValueError(f"Agent profile is disabled; recovery incomplete at project {registered['id']}; committed recovery: {recovered}. Retry Delete after resolving: {error}") from error
    return {'recovery':recovered,'tasks_recovered':sum(len(r['tasks']) for r in recovered),'file_reservations_released':sum(r['files_released'] for r in recovered)}


def recover_agent_tasks(db,target,actor):
    from store import now, activity
    import telemetry
    stamp=now();tasks=[]
    rows=db.execute("SELECT * FROM tasks WHERE owner_id=? AND status NOT IN ('Done','Cancelled')",(target['id'],)).fetchall()
    for task in rows:
        blocked=bool(json.loads(task['dependencies']) or task['blocked_reason'].strip())
        status='Blocked' if blocked and task['status'] in ('TODO','In Progress','Blocked') else 'TODO' if task['status'] in ('TODO','In Progress','Blocked') else task['status']
        db.execute('UPDATE tasks SET owner_id=NULL,lock_token=NULL,lock_until=0,status=?,updated=? WHERE id=?',(status,stamp,task['id']))
        telemetry.transition(db,task['id'],status,actor)
        activity(db,task['id'],actor,'comment',f"Agent profile {target['name']} deleted; ownership and reservations recovered. Status: {status}. Task content and evidence preserved.")
        tasks.append(task['id'])
    # Invalidate any leftover lease, including malformed historical leases.
    db.execute('UPDATE tasks SET lock_token=NULL,lock_until=0 WHERE owner_id=?',(target['id'],))
    files=db.execute('DELETE FROM file_locks WHERE owner_id=?',(target['id'],)).rowcount
    db.execute("UPDATE agent_sessions SET status='offline',reason='Agent profile deleted',resume_reset=NULL,auto_resume_status='cancelled by profile deletion' WHERE actor=?",(target['id'],))
    db.execute('UPDATE users SET active=0 WHERE id=?',(target['id'],))
    return {'tasks':tasks,'files_released':files}


def check_user_deletion(db, target):
    if target['active'] and target['kind']=='human' and db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND kind='human'").fetchone()[0]<=1:
        raise ValueError('Cannot delete the last active human user')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
        check_live_user(db,target['id'])


def ensure_uids(db):
    for table in TABLES:
        if 'uid' not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
            db.execute(f'ALTER TABLE {table} ADD COLUMN uid TEXT')
        for row in db.execute(f'SELECT id FROM {table} WHERE uid IS NULL').fetchall():
            db.execute(f'UPDATE {table} SET uid=? WHERE id=?', (str(uuid.uuid4()), row['id']))
        db.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS uid_{table} ON {table}(uid)')


def register(folder, project_uuid, checkout, preferred_id=None):
    with registry() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM folders WHERE path=?', (str(folder),)).fetchone()
        if existing:
            if existing['uuid'] != project_uuid or existing['checkout'] != checkout:
                raise ValueError('Registered folder identity changed; forget it before reopening')
            return existing['id']
        if preferred_id is None:
            # Keep unmigrated legacy IDs reserved for migration continuity.
            pending = legacy_projects()
            maximum = max([p['id'] for p in pending] + [0])
            sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='folders'").fetchone()
            maximum = max(maximum, sequence[0] if sequence else 0)
            cur = db.execute('INSERT INTO folders(id,uuid,path,checkout) VALUES(?,?,?,?)', (maximum+1,project_uuid,str(folder),checkout))
        else:
            cur = db.execute('INSERT INTO folders(id,uuid,path,checkout) VALUES(?,?,?,?)', (preferred_id,project_uuid,str(folder),checkout))
        return cur.lastrowid


def entry(project):
    with registry() as db:
        row = db.execute('SELECT * FROM folders WHERE id=?', (project,)).fetchone()
    if not row:
        raise ValueError('Project is not registered; open its folder first')
    folder = folder_path(row['path'])
    if metadata(folder)['uuid'] != row['uuid']:
        raise ValueError('Registered project UUID changed')
    return dict(row), folder


def describe(row):
    result = dict(row)
    try:
        folder = folder_path(row['path'])
        meta = metadata(folder)
        if meta['uuid'] != row['uuid']:
            raise ValueError('Project UUID changed')
        result.update(name=meta['name'], description=meta.get('description',''), available=True)
    except (OSError, ValueError, KeyError) as error:
        result.update(name=Path(row['path']).name, description='', available=False, error=str(error))
    return result


def open_project(args, connect):
    from store import required, description
    folder = folder_path(args.get('path'))
    manifest = folder / '.scrum' / 'project.json'
    creating = 'name' in args
    if creating:
        if manifest.exists():
            raise ValueError('Folder already contains a project; use project_open')
        if (folder / '.scrum').exists():
            raise ValueError('Folder already contains .scrum metadata; inspect it before creating')
        meta = {'schema_version': VERSION, 'uuid': str(uuid.uuid4()), 'name': required(args['name'],'name',120), 'description': description(args.get('description',''))}
        if not atomic_json(manifest, meta, exclusive=True):
            raise ValueError('Folder already contains a project; use project_open')
    else:
        meta = metadata(folder)
    main, locks, checkout = ensure_layout(folder, meta['uuid'])
    with closing(connect(main, locks)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        sync_users(db)
        project = db.execute('SELECT * FROM projects').fetchall()
        if len(project) > 1:
            raise ValueError('Folder database must contain exactly one project')
        if not project:
            snapshot = folder / '.scrum' / 'snapshot.json'
            if not creating:
                if not snapshot.exists():
                    raise ValueError('No local database or snapshot to import')
                restore_into(db, read_json(snapshot), meta)
            else:
                db.execute('INSERT INTO projects(id,name,description,workspace_root) VALUES(1,?,?,?)', (meta['name'],meta.get('description',''),str(folder)))
        db.execute('UPDATE projects SET workspace_root=?', (str(folder),))
        ensure_uids(db)
    project_id = register(folder, meta['uuid'], checkout)
    if creating:
        export_project(project_id, connect)
    return dict(id=project_id, uuid=meta['uuid'], path=str(folder), checkout=checkout, name=meta['name'])


def portable_file(path, folder):
    if PureWindowsPath(path).is_absolute() or Path(path).is_absolute():
        try:
            return Path(path).resolve().relative_to(folder).as_posix()
        except ValueError:
            raise ValueError('Task file plan outside project folder cannot be exported') from None
    parts = PurePosixPath(path.replace('\\','/'))
    if '..' in parts.parts or parts.is_absolute() or PureWindowsPath(path).drive:
        raise ValueError('Unsafe file plan in snapshot')
    return parts.as_posix()


def snapshot_data(db, meta, folder):
    ensure_uids(db)
    project = dict(db.execute('SELECT * FROM projects').fetchone())
    user_ids = {r['id']: r['identity'] for r in db.execute('SELECT id,identity FROM users')}
    task_ids = {r['id']: r['uid'] for r in db.execute('SELECT id,uid FROM tasks')}
    from coordination import POLICY_DEFAULTS
    data = {'schema_version':VERSION, 'project_uuid':meta['uuid'], 'settings':{k:project[k] for k in ['max_active_per_agent','require_evidence',*POLICY_DEFAULTS]}, 'tables':{}, 'users':[]}
    used = set()
    for table in TABLES:
        rows = []
        for record in db.execute(f'SELECT * FROM {table} ORDER BY id'):
            row = dict(record)
            row.pop('id'); row.pop('project',None)
            row.pop('lock_token',None); row.pop('lock_until',None)
            for key in ['actor','recipient','owner_id']:
                if key in row:
                    row[key] = user_ids.get(row[key])
                    if row[key]:
                        used.add(row[key])
            if 'task' in row:
                row['task'] = task_ids[row['task']] if row['task'] is not None else None
            if table == 'tasks':
                row['attachments']=json.loads(row['attachments'])
                row['cost_data'] = json.loads(row['cost_data'])
                row['model_selection']=json.loads(row['model_selection'])
                row['acceptance_criteria'] = json.loads(row['acceptance_criteria'])
                row['dependencies'] = [task_ids[i] for i in json.loads(row['dependencies'])]
                row['checklist'] = json.loads(row['checklist'])
                row['files'] = [portable_file(p, folder) for p in json.loads(row['files'])]
            if table == 'notes':
                row['items'] = json.loads(row['items'])
            rows.append(row)
        data['tables'][table] = rows
    data['users'] = [dict(r) for r in db.execute('SELECT identity,name,kind FROM users ORDER BY identity') if r['identity'] in used]
    return data


def export_project(project, connect):
    registered, folder = entry(project)
    meta = metadata(folder)
    main, locks = local_paths(folder)
    with closing(connect(main, locks)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        data = snapshot_data(db, meta, folder)
        row = db.execute('SELECT name,description FROM projects').fetchone()
        meta.update(name=row['name'], description=row['description'])
    # Snapshot has no export timestamp: identical data produces identical bytes.
    atomic_json(folder / '.scrum' / 'snapshot.json', data)
    atomic_json(folder / '.scrum' / 'project.json', meta)
    result={'exported':True, 'project':project, 'path':str(folder / '.scrum' / 'snapshot.json'), 'counts':{t:len(rows) for t,rows in data['tables'].items()}}
    references=sum(len(t.get('attachments',[])) for t in data['tables']['tasks'])
    if references:result['attachments']={'count':references,'content_included':False,'warning':'JSON exports preserve reference metadata only. Copy .scrum/local/attachments separately; imported missing content is reported by task_attachment.'}
    return result


def validate_snapshot(data, meta):
    from store import required, checklist, priority, description, STATUSES
    if not isinstance(data,dict) or data.get('schema_version') != VERSION or data.get('project_uuid') != meta['uuid']:
        raise ValueError('Snapshot format or project UUID mismatch')
    if not isinstance(data.get('tables'),dict) or set(data['tables']) != set(TABLES):
        raise ValueError('Invalid snapshot tables')
    settings = data.get('settings',{})
    from coordination import POLICY_DEFAULTS, validate_policy
    if not {'max_active_per_agent','require_evidence'} <= set(settings) or set(settings)-{'max_active_per_agent','require_evidence'}-set(POLICY_DEFAULTS) or type(settings['max_active_per_agent']) is not int or not 1 <= settings['max_active_per_agent'] <= 20 or settings['require_evidence'] not in [0,1] or type(settings['require_evidence']) not in [bool,int]:
        raise ValueError('Invalid snapshot settings')
    validate_policy(settings)
    users = data.get('users')
    if not isinstance(users,list):
        raise ValueError('Invalid snapshot users')
    identities_seen = set()
    for user in users:
        if not isinstance(user,dict) or set(user) != {'identity','name','kind'} or user['kind'] not in ['human','agent']:
            raise ValueError('Invalid snapshot user')
        required(user['identity'],'identity',160); required(user['name'],'name',120)
        if user['identity'] in identities_seen:
            raise ValueError('Duplicate user identity')
        identities_seen.add(user['identity'])
    ids = {}
    for table in TABLES:
        rows = data['tables'][table]
        if not isinstance(rows,list) or len(rows)>100000:
            raise ValueError('Invalid snapshot row count')
        ids[table] = set()
        for row in rows:
            if not isinstance(row,dict):
                raise ValueError('Invalid snapshot row')
            valid_uuid(row.get('uid'))
            if row['uid'] in ids[table]:
                raise ValueError('Duplicate entity UUID')
            ids[table].add(row['uid'])
            for key in ['actor','recipient','owner_id']:
                if row.get(key) is not None and row[key] not in identities_seen:
                    raise ValueError('Snapshot references an unknown author')
    numbers = set()
    graph = {}
    for row in data['tables']['tasks']:
        import model_routing
        from store import validate_context_group
        validate_context_group(row.get('context_group',''))
        model_routing.validate(row.get('model',''),row.get('model_tier','auto'),row.get('model_reason',''))
        if not isinstance(row.get('model_selection',{}),dict):raise ValueError('Invalid model selection')
        from store import validate_verification
        validate_verification(row.get('verification_level','auto'),row.get('verification_reason',''))
        from store import acceptance_criteria
        from attachments import validate_metadata
        validate_metadata(row.get('attachments',[]))
        from coordination import validate_scope
        import task_cost
        task_cost.validate(row.get('cost_data',task_cost.empty()))
        validate_scope(row.get('scope_size','Unknown'),row.get('scope_reason',''))
        for key,limit in [('continuity',2000),('continuity_basis',24)]:
            if not isinstance(row.get(key,''),str) or len(row.get(key,''))>limit:
                raise ValueError('Invalid continuity data')
        acceptance_criteria(row.get('acceptance_criteria',[]),allow_empty=True)
        if row.get('status') not in STATUSES or type(row.get('number')) is not int or row['number'] < 1 or row['number'] in numbers:
            raise ValueError('Invalid or duplicate task number/status; resolve Git conflicts first')
        numbers.add(row['number'])
        required(row.get('title'),'title',240); priority(row.get('priority')); checklist(row.get('checklist'))
        required(row.get('category'),'category',80)
        for field in ['description','blocked_reason']:
            description(row.get(field))
        for field,limit in [('evidence',4000),('cancel_reason',2000)]:
            if not isinstance(row.get(field),str) or len(row[field])>limit:
                raise ValueError('Invalid task evidence/reason')
        if not isinstance(row.get('dependencies'),list) or len(row['dependencies'])>100 or any(i not in ids['tasks'] for i in row['dependencies']):
            raise ValueError('Invalid dependency reference')
        if not isinstance(row.get('files'),list) or len(row['files'])>100:
            raise ValueError('Invalid file plan')
        for path in row['files']:
            if not isinstance(path,str) or not path or len(path)>1024 or '\x00' in path or PureWindowsPath(path).is_absolute() or Path(path).is_absolute():
                raise ValueError('Snapshot file plans must be relative')
            portable_file(path, Path.cwd())
        graph[row['uid']] = row['dependencies']
    visited, visiting = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError('Snapshot dependencies contain a cycle')
        if node in visited:
            return
        visiting.add(node)
        for child in graph[node]:
            visit(child)
        visiting.remove(node); visited.add(node)
    for node in graph:
        visit(node)
    for row in data['tables']['notes']:
        if row.get('audience','human') not in ('human','agent') or (row.get('audience')=='agent' and (row.get('task') is None or row.get('kind')!='Review')):raise ValueError('Invalid note audience')
        if row.get('task') is not None and (not isinstance(row['task'],str) or row['task'] not in ids['tasks']):raise ValueError('Invalid note task reference')
        if row.get('kind') not in ['Daily','Review','Retrospective'] or type(row.get('done')) is not int or row['done'] not in [0,1] or not isinstance(row.get('items'),list) or len(row['items'])>100:
            raise ValueError('Invalid note')
        required(row.get('title'),'title',240)
        for item in row['items']:
            required(item,'item',4000)
    for table in ['task_activity','task_states','project_messages']:
        for row in data['tables'][table]:
            if row.get('task') not in ids['tasks'] and not (table == 'project_messages' and row.get('task') is None):
                raise ValueError('Invalid task history reference')
            if table in ['task_activity','project_messages']:
                required(row.get('text'),'text',4000 if table=='task_activity' else 2000)
            if table == 'project_messages' and row.get('actor') is None:
                raise ValueError('Chat messages require an author')
    intervals = {}
    for row in data['tables']['task_states']:
        if row.get('status') not in STATUSES or type(row.get('entered')) not in [int,float] or (row.get('exited') is not None and (type(row['exited']) not in [int,float] or row['exited']<row['entered'])):
            raise ValueError('Invalid state interval')
        intervals.setdefault(row['task'],[]).append(row)
    for row in data['tables']['tasks']:
        states = sorted(intervals.get(row['uid'],[]),key=lambda s:s['entered'])
        if not states or states[-1]['exited'] is not None or states[-1]['status']!=row['status'] or any(s['exited'] is None or s['exited']>states[i+1]['entered'] for i,s in enumerate(states[:-1])):
            raise ValueError('Inconsistent task state history')


def restore_into(db, data, meta):
    validate_snapshot(data, meta)
    for row in data['tables']['notes']:
        row.setdefault('task',None)
        row.setdefault('audience','human')
    for row in data['tables']['tasks']:
        row.setdefault('attachments',[])
        row.setdefault('acceptance_criteria',[])
        row.setdefault('continuity','')
        row.setdefault('continuity_basis','')
        import task_cost
        row.setdefault('cost_data',task_cost.empty())
        row.setdefault('verification_level','auto')
        row.setdefault('context_group','')
        row.setdefault('verification_reason','')
        row.setdefault('scope_size','Unknown')
        row.setdefault('scope_reason','')
        for key,value in [('model',''),('model_tier','auto'),('model_reason',''),('model_selection',{})]:row.setdefault(key,value)
    # Whitelisted database columns reject forged SQL/unknown session fields.
    for table, rows in data['tables'].items():
        columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')} | {'uid'}
        excluded = {'id','project','lock_token','lock_until'}
        for row in rows:
            if set(row) != columns-excluded:
                raise ValueError(f'Unexpected/missing snapshot fields in {table}')
    user_map = {u['identity']:identities('user_register',u,historical=True)['id'] for u in data['users']}
    ensure_uids(db)
    for table in ['project_messages','task_states','task_activity','notes','tasks','projects']:
        db.execute(f'DELETE FROM {table}')
    db.execute('DELETE FROM file_locks')
    db.execute('DELETE FROM agent_sessions')
    db.execute('DELETE FROM project_control')
    for table in ('worker_launch_offers','context_batches','context_batch_history'):
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():db.execute('DELETE FROM '+table)
    db.execute('DELETE FROM users')
    sync_users(db)
    db.execute('INSERT INTO projects(id,name,description,max_active_per_agent,require_evidence) VALUES(1,?,?,?,?)', (meta['name'],meta.get('description',''),data['settings']['max_active_per_agent'],data['settings']['require_evidence']))
    from coordination import POLICY_DEFAULTS
    for key,default in POLICY_DEFAULTS.items():
        db.execute(f'UPDATE projects SET {key}=?',(data['settings'].get(key,default),))
    task_map = {row['uid']:i+1 for i,row in enumerate(data['tables']['tasks'])}
    for table in TABLES:
        for record in data['tables'][table]:
            row = dict(record)
            for key in ['actor','recipient','owner_id']:
                if key in row:
                    row[key] = user_map.get(row[key])
            if table in ['tasks','notes','project_messages']:
                row['project'] = 1
            if 'task' in row:
                row['task'] = task_map.get(row['task'])
            if table == 'tasks':
                row['attachments']=json.dumps(row['attachments'])
                row['cost_data'] = json.dumps(row['cost_data'])
                row['model_selection']=json.dumps(row['model_selection'])
                row['acceptance_criteria'] = json.dumps(row['acceptance_criteria'])
                row['id'] = task_map[row['uid']]
                row['dependencies'] = json.dumps([task_map[i] for i in row['dependencies']])
                row['checklist'] = json.dumps(row['checklist'])
                row['files'] = json.dumps(row['files'])
            if table == 'notes':
                row['items'] = json.dumps(row['items'])
            keys = list(row)
            db.execute(f'INSERT INTO {table}('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')', [row[k] for k in keys])
    errors = db.execute('PRAGMA foreign_key_check').fetchall()
    if errors:
        raise ValueError('Snapshot has broken relationships')


def restore_project(project, args, connect):
    if args.get('confirm') is not True:
        raise ValueError('Restoring replaces local work; supply confirm=true explicitly')
    _, folder = entry(project)
    meta = metadata(folder)
    data = read_json(folder / '.scrum' / 'snapshot.json')
    main, locks = local_paths(folder)
    with closing(connect(main, locks)) as db:
        if db.execute('SELECT 1 FROM tasks WHERE lock_until>?', (time.time(),)).fetchone():
            raise ValueError('Release live task claims before restoring')
        backup = main.parent / ('backup-'+str(time.time_ns())+'.db')
        with closing(sqlite3.connect(backup)) as saved:
            db.backup(saved)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM tasks WHERE lock_until>?', (time.time(),)).fetchone():
                    raise ValueError('Release live task claims before restoring')
                restore_into(db, data, meta)
                db.execute('UPDATE projects SET workspace_root=?', (str(folder),))
        except (sqlite3.Error, KeyError, TypeError) as error:
            raise ValueError('Invalid snapshot; local database was preserved') from error
    return {'restored':True,'project':project,'backup':str(backup)}


def rewrite_project(value, project):
    if isinstance(value,list):
        return [rewrite_project(v,project) for v in value]
    if isinstance(value,dict):
        return {k:project if k=='project' else rewrite_project(v,project) for k,v in value.items() if k != 'uid'}
    return value


def dispatch(action, args, legacy_run, connect):
    # Explicit SCRUM_DB remains a legacy/single-file compatibility mode for
    # older integrations. SCRUM_REGISTRY selects isolated mode even with it set.
    compatibility = 'SCRUM_DB' in os.environ and 'SCRUM_REGISTRY' not in os.environ
    legacy_path = Path(os.environ.get('SCRUM_DB', ROOT / 'data' / 'scrum.db'))
    legacy = compatibility or (not registry_path().exists() and legacy_path.exists() and not legacy_path.with_suffix('.migrated.json').exists())
    if action == 'project_migrate':
        return migrate_legacy(args, legacy_path, connect)
    if legacy and action not in PORTABLE_ACTIONS and not (action == 'project_create' and 'path' in args):
        result = legacy_run(action,args)
        if action == 'projects':
            return [dict(p, legacy=True, available=True, path=p.get('workspace_root','')) for p in result]
        return result
    if action == 'projects':
        with registry() as db:
            registered = [describe(r) for r in db.execute('SELECT * FROM folders ORDER BY id')]
        known = {r['id'] for r in registered}
        return registered + [dict(p, legacy=True, available=False, error='Legacy project requires folder migration') for p in legacy_projects() if p['id'] not in known]
    if action in ['users','user_register','user_manage']:
        return identities(action,args)
    if action in ['project_create','project_open']:
        return open_project(args,connect)
    project = args.get('project')
    if action == 'project_export':
        return export_project(project,connect)
    if action == 'project_restore':
        return restore_project(project,args,connect)
    if action == 'project_forget':
        with registry() as db:
            registered = db.execute('SELECT * FROM folders WHERE id=?', (project,)).fetchone()
        if not registered:
            raise ValueError('Project is not registered')
        folder = Path(registered['path'])
        if folder.exists():
            main, locks = local_paths(folder_path(str(folder)))
            with closing(connect(main, locks)) as db:
                if db.execute('SELECT 1 FROM tasks WHERE lock_until>?', (time.time(),)).fetchone():
                    raise ValueError('Release live task claims before forgetting a project')
        with registry() as db:
            db.execute('DELETE FROM folders WHERE id=?', (project,))
        return {'forgotten':True,'project':project,'files_deleted':False}
    registered, folder = entry(project)
    main, locks = local_paths(folder)
    with closing(connect(main, locks)) as db, db:
        sync_users(db)
        local = db.execute('SELECT id FROM projects').fetchone()
        if not local:
            raise ValueError('Project database is empty; reopen/import its folder')
        local_project = local['id']
    if action == 'project_settings' and 'workspace_root' in args and args['workspace_root'] != str(folder):
        raise ValueError('Workspace root is the registered folder; move/reopen the project instead')
    result = legacy_run(action,dict(args,project=local_project),main,locks)
    return rewrite_project(result,project)


def legacy_projects():
    source = Path(os.environ.get('SCRUM_DB', ROOT / 'data' / 'scrum.db'))
    if not source.exists():
        return []
    marker = source.with_suffix('.migrated.json')
    migrated = read_json(marker) if marker.exists() else {}
    with closing(sqlite3.connect(source)) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='projects'").fetchone():
            return []
        return [dict(id=r['id'], name=r['name'], description=r['description'],path=dict(r).get('workspace_root','')) for r in db.execute('SELECT * FROM projects ORDER BY id') if str(r['id']) not in migrated]


def migrate_legacy(args, source, connect):
    """Stage a full copy before publishing the project folder and routing."""
    if not source.exists():
        raise ValueError('Legacy database not found')
    project = args.get('project')
    folder = folder_path(args.get('path'))
    if (folder / '.scrum').exists():
        raise ValueError('Target already has .scrum; migration refuses to overwrite it')
    marker = source.with_suffix('.migrated.json')
    migrated = read_json(marker) if marker.exists() else {}
    if str(project) in migrated:
        raise ValueError('Project was already migrated; open its existing folder')
    staging = Path(tempfile.mkdtemp(prefix='.scrum-migration-', dir=folder))
    try:
        with closing(connect(source)) as old:
            old.execute('BEGIN IMMEDIATE')
            migrated = read_json(marker) if marker.exists() else {}
            if str(project) in migrated:
                raise ValueError('Project was already migrated; open its existing folder')
            row = old.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone()
            if not row:
                raise ValueError('Legacy project not found')
            backup = source.with_name(source.stem+'-backup-'+str(time.time_ns())+'.db')
            with closing(sqlite3.connect(source)) as reader, closing(sqlite3.connect(backup)) as saved:
                reader.backup(saved)
            users = [dict(r) for r in old.execute('SELECT * FROM users ORDER BY id')]
            for user in users:
                registered = identities('user_register',user,historical=True)
                if registered['id'] != user['id']:
                    raise ValueError('Legacy actor IDs conflict with local identities; export/import required')
            meta = {'schema_version':VERSION,'uuid':str(uuid.uuid4()),'name':row['name'],'description':row['description']}
            main, locks, checkout = ensure_layout(staging,meta['uuid'])
            with closing(connect(main,locks)) as target, target:
                target.execute('BEGIN IMMEDIATE')
                for user in users:
                    target.execute('INSERT INTO users(id,identity,name,kind) VALUES(:id,:identity,:name,:kind)',user)
                columns = list(row.keys())
                target.execute('INSERT INTO projects('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row))
                target.execute('UPDATE projects SET workspace_root=?',(str(folder),))
                for table in TABLES:
                    if table in ['task_activity','task_states']:
                        rows = old.execute(f'SELECT h.* FROM {table} h JOIN tasks t ON t.id=h.task WHERE t.project=?',(project,)).fetchall()
                    else:
                        rows = old.execute(f'SELECT * FROM {table} WHERE project=?',(project,)).fetchall()
                    for record in rows:
                        columns = list(record.keys())
                        target.execute(f'INSERT INTO {table}('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(record))
                for record in old.execute('SELECT * FROM file_locks WHERE project=?',(project,)):
                    columns = list(record.keys())
                    target.execute('INSERT INTO file_locks('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(record))
                ensure_uids(target)
                if target.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('Legacy migration contains broken relationships')
                data = snapshot_data(target,meta,folder)
                validate_snapshot(data,meta)
            atomic_json(staging / '.scrum' / 'project.json',meta)
            atomic_json(staging / '.scrum' / 'snapshot.json',data)
            if (folder / '.scrum').exists():
                raise ValueError('Target metadata appeared during migration; source preserved')
            (staging / '.scrum').rename(folder / '.scrum')
            project_id = register(folder,meta['uuid'],checkout,preferred_id=project)
            migrated[str(project)] = meta['uuid']
            atomic_json(marker,migrated)
            old.rollback()
    finally:
        # Only remove the private staging directory this invocation created.
        if staging.parent == folder and staging.name.startswith('.scrum-migration-'):
            shutil.rmtree(staging)
    return {'migrated':True,'id':project_id,'uuid':meta['uuid'],'path':str(folder),'backup':str(backup),'restart_required':True}
