"""Human task decisions; feedback never grants provider or sandbox permissions."""
import hashlib
import json
import time
import re
import sqlite3
from pathlib import PurePosixPath


FINDING_STATES=('open','fixed','false_positive','intentional','deferred','duplicate')


def findings(db,task,args):
    """Keep a stable issue key across revisions and preserve its transitions."""
    db.execute("""CREATE TABLE IF NOT EXISTS review_findings (
        task INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        key TEXT NOT NULL, path TEXT NOT NULL, summary TEXT NOT NULL, revision TEXT NOT NULL,
        state TEXT NOT NULL, disposition TEXT NOT NULL, updated REAL NOT NULL,
        PRIMARY KEY(task,key,path)
    )""")
    columns={column['name'] for column in db.execute('PRAGMA table_info(review_findings)')}
    for name,definition in (('severity',"TEXT NOT NULL DEFAULT 'normal'"),('reporter','INTEGER'),
                            ('confirmed_by','INTEGER'),('proof',"TEXT NOT NULL DEFAULT ''"),
                            ('duplicate_of','TEXT')):
        if name not in columns:
            try:db.execute(f'ALTER TABLE review_findings ADD COLUMN {name} {definition}')
            except sqlite3.OperationalError:
                if name not in {column['name'] for column in db.execute('PRAGMA table_info(review_findings)')}:
                    raise
    action=args.get('action','list')
    limit=args.get('limit',20);after=args.get('after',0)
    if type(limit) is not int or not 1<=limit<=50 or type(after) is not int or after<0:
        raise ValueError('Invalid findings page')
    if action=='list':
        states=FINDING_STATES if args.get('include_resolved') else ('open','deferred')
        rows=db.execute('SELECT key,path,summary,revision,state,disposition,updated,severity,confirmed_by,proof,duplicate_of FROM review_findings '
                        'WHERE task=? AND state IN ('+','.join('?' for _ in states)+') '
                        'ORDER BY key,path LIMIT ? OFFSET ?',(task,*states,limit+1,after)).fetchall()
        return {'items':[dict(row) for row in rows[:limit]],'has_more':len(rows)>limit,'next_after':after+min(len(rows),limit)}
    key=args.get('key','')
    path=args.get('path','');normalized=PurePosixPath(path)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',key) or not normalized.parts or normalized.is_absolute() or '..' in normalized.parts or normalized.as_posix()!=path or any(c in path for c in ('\\',':','\x00')):
        raise ValueError('Use a stable issue key and repository-relative file path')
    revision=args.get('revision','')
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',revision):
        raise ValueError('Supply the Git revision reviewed')
    existing=db.execute('SELECT * FROM review_findings WHERE task=? AND key=? AND path=?',(task,key,path)).fetchone()
    if action in ('confirm','consolidate'):
        if not existing or existing['state'] not in ('open','deferred') or existing['revision']!=revision:
            raise ValueError('Reload the open finding for this exact revision')
        if action=='confirm':
            proof=args.get('proof','').strip()
            if existing['severity']!='high' or not proof or existing['reporter'] in (None,args.get('actor')):
                raise ValueError('A high-severity finding needs reproduction or trace evidence from a different reviewer')
            db.execute('UPDATE review_findings SET confirmed_by=?,proof=?,updated=? WHERE task=? AND key=? AND path=?',
                       (args['actor'],proof,time.time(),task,key,path))
            return {'key':key,'path':path,'state':existing['state'],'confirmed':True,
                    'activity':f'Confirmed review finding {key} at {revision[:12]}: {proof}'}
        canonical=args.get('canonical_key')
        primary=db.execute('SELECT * FROM review_findings WHERE task=? AND key=? AND path=?',(task,canonical,path)).fetchone()
        reason=args.get('disposition','').strip()
        if not primary or primary['key']==key or primary['revision']!=revision or primary['state'] not in ('open','deferred') or not reason:
            raise ValueError('Select a canonical open finding on the same path and revision and explain the equivalence')
        if existing['confirmed_by'] and not (primary['confirmed_by'] and primary['severity']=='high'):
            raise ValueError('Confirm the canonical high-severity finding before consolidating this confirmed finding')
        db.execute("UPDATE review_findings SET state='duplicate',duplicate_of=?,disposition=?,updated=? WHERE task=? AND key=? AND path=?",
                   (canonical,reason,time.time(),task,key,path))
        return {'key':key,'path':path,'state':'duplicate','duplicate_of':canonical,
                'activity':f'Consolidated {key} into {canonical} at {revision[:12]}: {reason}'}
    if action=='record':
        summary=args.get('summary','').strip()
        severity=args.get('severity','normal')
        if not summary or severity not in ('normal','high'):raise ValueError('Describe the observed finding and its severity')
        if existing and existing['state']!='fixed':
            return {'key':key,'path':path,'state':existing['state'],'repeat':True}
        if existing and existing['revision']==revision:
            return {'key':key,'path':path,'state':existing['state'],'repeat':True}
        state='open';disposition=''
    else:
        if action!='resolve' or not existing or existing['state']=='duplicate':raise ValueError('Finding does not exist or is consolidated')
        state=args.get('state');disposition=args.get('disposition','').strip()
        if state not in FINDING_STATES[1:-1] or not disposition:raise ValueError('Supply a finding outcome and its evidence')
        if existing['confirmed_by'] and revision!=existing['revision'] and state=='deferred':
            raise ValueError('Keep the confirmed revision when deferring the finding')
        summary=existing['summary']
    updated=time.time()
    db.execute('INSERT INTO review_findings(task,key,path,summary,revision,state,disposition,updated) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task,key,path) DO UPDATE SET '
               'summary=excluded.summary,revision=excluded.revision,state=excluded.state,disposition=excluded.disposition,updated=excluded.updated',
               (task,key,path,summary,revision,state,disposition,updated))
    if action=='record':
        db.execute("UPDATE review_findings SET severity=?,reporter=?,confirmed_by=NULL,proof='',duplicate_of=NULL WHERE task=? AND key=? AND path=?",
                   (severity,args.get('actor'),task,key,path))
    return {'key':key,'path':path,'state':state,'repeat':False,
            'activity':f'Review finding {key} ({path}) at {revision[:12]}: {state}. {disposition or summary}'}


def data(row):
    raw=dict(row).get('review_data','{}')
    return json.loads(raw) if isinstance(raw,str) else raw


def validate(value):
    if not isinstance(value,dict) or set(value)-{'required','state','comment','author','name','at'}:
        raise ValueError('Invalid task review metadata')
    if not value:return value
    if value.get('required') is not True or value.get('state') not in ('pending','changes_requested','approved'):
        raise ValueError('Invalid task review state')
    for key,limit in [('comment',1500),('author',160),('name',120),('at',80)]:
        if not isinstance(value.get(key,''),str) or len(value.get(key,''))>limit:raise ValueError('Invalid review '+key)
    return value


def version(row):
    row=dict(row)
    fields=('status','title','description','acceptance_criteria','checklist','files','evidence','attachments','review_data','updated','owner_id')
    return hashlib.sha256(json.dumps({k:row.get(k) for k in fields},sort_keys=True).encode()).hexdigest()


def view(row):
    value=data(row)
    if not value and row['status']!='In Review':return None
    return {**value,'version':version(row),'awaiting_human':row['status']=='In Review' and value.get('state')!='approved'}


def prepare(db,args,old,project_row,actor_user,now,activity):
    if set(args)-{'project','id','actor','decision','comment','expected_version','measurement_session'}:raise ValueError('Unsupported review fields')
    actor=actor_user(db,args)
    if actor['kind']!='human':raise ValueError('Only an active human can review a task')
    if old['status']!='In Review':raise ValueError('Task is no longer In Review; reload it before reviewing')
    if old['lock_until']>time.time():raise ValueError('Wait for the current task claim to close before reviewing')
    if args.get('expected_version') and args['expected_version']!=version(old):raise ValueError('Task changed; reload the task and its attachments before reviewing')
    decision=args.get('decision');comment=args.get('comment','')
    if decision not in ('approve','request_changes'):raise ValueError('Review decision must be approve or request_changes')
    if not isinstance(comment,str) or len(comment)>1500:raise ValueError('Review feedback must be text, max1500 characters')
    comment=comment.strip()
    if decision=='request_changes' and not comment:raise ValueError('Describe the requested changes before submitting')
    if decision=='approve':
        check_confirmed_findings(db,old,project_row)
    review=dict(required=True,state='approved' if decision=='approve' else 'changes_requested',comment=comment,author=actor['identity'],name=actor['name'],at=now())
    db.execute('UPDATE tasks SET review_data=? WHERE id=?',(json.dumps(review),old['id']))
    update={k:args[k] for k in ('project','id','actor','measurement_session') if k in args}
    update.update(status='Done' if decision=='approve' else 'TODO',log=('Human approved this task.' if decision=='approve' else 'Human requested changes: '+comment))
    if decision=='approve':update['evidence']=old['evidence']
    if decision=='request_changes':
        if old['evidence']:activity(db,old['id'],actor['id'],'comment','Superseded verification before requested changes:\n'+old['evidence'])
        update.update(evidence='',continuity='Human requested changes:\n'+comment+'\nNext: revise the existing deliverable, preserve unrelated work, verify the changes and return it for human review.')
        import runtime
        control=runtime.control(db,project_row['id'])
        if not control['paused']:
            db.execute("INSERT INTO project_control(project,paused,generation,reason,changed,actor) VALUES(?,0,1,?,?,?) ON CONFLICT(project) DO UPDATE SET generation=project_control.generation+1,reason=excluded.reason,changed=excluded.changed,actor=excluded.actor WHERE project_control.paused=0",(project_row['id'],'Human review requested changes',time.time(),actor['id']))
    return update


def check_confirmed_findings(db, task, project):
    columns={row['name'] for row in db.execute('PRAGMA table_info(review_findings)')}
    if 'confirmed_by' not in columns:return
    blockers=db.execute("SELECT key,revision FROM review_findings WHERE task=? AND severity='high' "
                        "AND confirmed_by IS NOT NULL AND state IN ('open','deferred')",(task['id'],)).fetchall()
    if not blockers:return
    import coordination
    import git_sync
    import git_worktrees
    from store import project_prefix

    root=git_worktrees.workspace(project['workspace_root'],git_worktrees.identity(task))
    if not root:raise ValueError('The reviewed checkout is unavailable. Resolve the confirmed findings before approval.')
    settings=coordination.settings(project)
    reference='HEAD'
    if git_sync.enabled(settings):
        reference='refs/heads/'+git_sync.branch_for(settings,{
            'code':f"{project_prefix(project['name'])}-{task['number']}",'title':task['title'],
            'context_group':task['context_group']})
    tip=git_sync._git(root,'rev-parse','--verify',reference)
    if tip.returncode:
        raise ValueError('The delivery revision could not be verified. Resolve the confirmed findings before approval.')
    pending=[row['key'] for row in blockers if row['revision']==tip.stdout.strip()]
    if pending:raise ValueError('Resolve confirmed high-severity findings for this revision: '+', '.join(pending[:10]))


def transition(db,old,args,task,claiming,completing):
    review=data(old)
    if args.get('status')=='In Review':review={**review,'required':True,'state':'pending'}
    if claiming and review.get('required'):review={**review,'state':'pending'}
    awaiting=completing and review.get('required') and review.get('state')!='approved'
    if awaiting:review={**review,'state':'pending'}
    if review!=data(old):db.execute('UPDATE tasks SET review_data=? WHERE id=?',(json.dumps(review),old['id']))
    return bool(awaiting)
