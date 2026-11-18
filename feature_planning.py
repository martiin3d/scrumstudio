"""Local feature requests. Model output is a draft until a human applies it."""

import json
from pathlib import Path
import re
import subprocess
import sys
import time

import coordination
import planning
import runtime

PRIVATE = object()
LEASE_SECONDS = 240
TASK_FIELDS = ('key', 'title', 'description', 'acceptance_criteria', 'files', 'dependencies',
               'scope_size', 'scope_reason', 'required_skills', 'specification')


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS feature_requests (
        project INTEGER NOT NULL, id TEXT NOT NULL, author INTEGER NOT NULL,
        request TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
        attempt INTEGER NOT NULL, deadline REAL NOT NULL, updated REAL NOT NULL,
        planner INTEGER, result TEXT NOT NULL DEFAULT '{}', tasks TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (project, id))''')


def active_actors(db, project):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='feature_requests'").fetchone():
        return set()
    return {row[0] for row in db.execute(
        "SELECT planner FROM feature_requests WHERE project=? AND state='running' AND deadline>?", (project,time.time()))}


def live_state(row, now):
    return 'failed' if row['state'] in ('queued','running') and row['deadline']<=now else row['state']


def view(row):
    state=live_state(row,time.time())
    result=json.loads(row['result'])
    if state!=row['state']:
        result={'message':'The planner stopped reporting. You can retry the same request.'}
    return {**{key:row[key] for key in ('id','request','answer','attempt','updated')},
            'state':state,'result':result,'tasks':json.loads(row['tasks'])}


def validate_draft(value):
    from store import required, acceptance_criteria, validate_required_skills

    if not isinstance(value,dict) or set(value)!={'message','question','items'}:
        raise ValueError('Planner returned an invalid draft')
    message=required(value['message'],'planner response',1500)
    question=value['question']
    items=value['items']
    if not isinstance(question,str) or len(question)>1000 or not isinstance(items,list) or len(items)>10 or bool(question.strip())==bool(items):
        raise ValueError('Planner must return one question or 1–10 task proposals')
    for task in items:
        if not isinstance(task,dict) or set(task)!=set(TASK_FIELDS):raise ValueError('Planner returned invalid task fields')
        for field,limit in [('key',80),('title',120),('description',4000),('scope_reason',500)]:
            required(task[field],field,limit)
        acceptance_criteria(task['acceptance_criteria'])
        coordination.file_plan(task['files'])
        validate_required_skills(task['required_skills'])
        if task['scope_size'] not in ('Small','Large') or not isinstance(task['dependencies'],list) or any(not isinstance(key,str) for key in task['dependencies']):
            raise ValueError('Planner returned invalid scope or dependencies')
        if task['specification'] is not None and (not isinstance(task['specification'],dict) or set(task['specification'])!={'page','section'} or not all(isinstance(v,str) for v in task['specification'].values())):
            raise ValueError('Planner returned an invalid wiki reference')
    return {'message':message,'question':question.strip(),'items':items}


def operation(db, project, args):
    from store import actor_user, required

    kind=args.get('action','list')
    if kind in ('begin','finish'):
        if args.get('capability') is not PRIVATE:raise ValueError('Internal planner action')
    elif kind not in ('list','submit','answer','retry','apply'):
        raise ValueError('Unknown feature planning action')
    if kind in ('submit','answer','retry') and not runtime.control(db,project)['enabled']:
        raise ValueError('SCRUM is off for this project. Turn it on before asking the planner.')
    ensure(db)
    if kind=='list':
        return {'requests':[view(row) for row in db.execute('SELECT * FROM feature_requests WHERE project=? ORDER BY updated DESC LIMIT 20',(project,))]}
    if kind not in ('begin','finish') and actor_user(db,args)['kind']!='human':
        raise ValueError('Choose an active human profile to manage feature requests')
    request_id=args.get('id')
    if not isinstance(request_id,str) or not re.fullmatch('[a-f0-9]{32}',request_id):raise ValueError('Invalid feature request ID')
    db.execute('BEGIN IMMEDIATE')
    now=time.time()
    def current():
        return db.execute('SELECT * FROM feature_requests WHERE project=? AND id=?',(project,request_id)).fetchone()
    row=current()
    if kind=='submit':
        text=required(args.get('text'),'feature request',4000)
        if row:
            if row['request']!=text:raise ValueError('Request ID already exists with different text')
            return view(row)
        db.execute("INSERT INTO feature_requests(project,id,author,request,state,attempt,deadline,updated) VALUES (?,?,?,?,'queued',1,?,?)",
                   (project,request_id,args['actor'],text,now+LEASE_SECONDS,now))
        return {**view(current()),'_launch':True}
    if not row:raise ValueError('Feature request not found in this project')
    state=live_state(row,now)
    if kind in ('answer','retry'):
        allowed=('question','ready') if kind=='answer' else ('failed',)
        if state not in allowed or args.get('attempt')!=row['attempt']:raise ValueError('Reload the current request before sending an answer or retrying')
        answer=row['answer']
        if kind=='answer':
            previous=json.loads(row['result'])
            question=previous['question'] or 'Previous draft · '+', '.join(item['title'] for item in previous['items'])
            answer=required((answer+'\n\n' if answer else '')+question+'\n'+required(args.get('text'),'answer',1500),'clarifications',6000)
        db.execute("UPDATE feature_requests SET answer=?,state='queued',attempt=attempt+1,deadline=?,updated=?,planner=NULL,result='{}' WHERE project=? AND id=?",
                   (answer,now+LEASE_SECONDS,now,project,request_id))
        return {**view(current()),'_launch':True}
    if kind=='apply':
        if state=='applied':return view(row)
        if state!='ready' or args.get('attempt')!=row['attempt']:raise ValueError('Review the current task plan before applying it')
        items=[{key:value for key,value in task.items() if value is not None} for task in json.loads(row['result'])['items']]
        tasks=planning.apply(db,{'actor':args['actor'],'items':items},project,begin=False)['items']
        db.execute("UPDATE feature_requests SET state='applied',tasks=?,updated=? WHERE project=? AND id=?",(json.dumps(tasks),now,project,request_id))
        return view(current())
    if row['attempt']!=args.get('attempt'):raise ValueError('The planner attempt was superseded')
    if kind=='begin':
        if state!='queued':raise ValueError('The request was already started or expired')
        planner=actor_user(db,args)['id']
        runtime.gate(db,project,planner)
        if active_actors(db,project) or not coordination.parallel_available(db,project,planner):raise ValueError('Planner is busy or the project agent limit is reached. Retry when a slot is free.')
        db.execute("UPDATE feature_requests SET state='running',planner=?,updated=?,deadline=? WHERE project=? AND id=?",
                   (planner,now,now+LEASE_SECONDS,project,request_id))
        return view(current())
    expected='running' if args.get('started') or 'draft' in args else 'queued'
    if state!=expected:raise ValueError('The planner attempt is no longer active')
    if 'error' in args:
        result={'message':required(args['error'],'planner failure',800)}; status='failed'
    else:
        runtime.gate(db,project,row['planner'])
        result=validate_draft(args.get('draft'))
        status='question' if result['question'] else 'ready'
    db.execute('UPDATE feature_requests SET result=?,state=?,updated=? WHERE project=? AND id=?',
               (json.dumps(result),status,now,project,request_id))
    return view(current())


def launch(project, result):
    script=Path(__file__).resolve().parent/'scripts'/'feature_planner.py'
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {'start_new_session':True}
    subprocess.Popen([sys.executable,str(script),str(project),result['id'],str(result['attempt'])],
                     cwd=script.parents[1],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
