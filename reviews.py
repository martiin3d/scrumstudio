"""Human task decisions; feedback never grants provider or sandbox permissions."""
import hashlib
import json
import time


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


def transition(db,old,args,task,claiming,completing):
    review=data(old)
    if args.get('status')=='In Review':review={**review,'required':True,'state':'pending'}
    if claiming and review.get('required'):review={**review,'state':'pending'}
    awaiting=completing and review.get('required') and review.get('state')!='approved'
    if awaiting:review={**review,'state':'pending'}
    if review!=data(old):db.execute('UPDATE tasks SET review_data=? WHERE id=?',(json.dumps(review),old['id']))
    return bool(awaiting)
