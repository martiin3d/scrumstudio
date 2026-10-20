"""Local, bounded human decisions. Never execute commands or grant permissions."""
import hashlib
import json
import time
import uuid
import runtime


def ensure(db):
    # Intentionally excluded from portable snapshots: callbacks belong to a live run.
    db.execute('''CREATE TABLE IF NOT EXISTS agent_interactions(
        id TEXT PRIMARY KEY, project INTEGER, actor INTEGER, task INTEGER,
        lease TEXT, kind TEXT, question TEXT, options TEXT, answer TEXT DEFAULT '',
        state TEXT DEFAULT 'pending', created TEXT, expires REAL)''')


def lease(task):
    return hashlib.sha256((task['lock_token'] or '').encode()).hexdigest()


def live(db, row):
    task=db.execute('SELECT * FROM tasks WHERE project=? AND id=?',(row['project'],row['task'])).fetchone()
    agent=db.execute('SELECT active FROM users WHERE id=?',(row['actor'],)).fetchone()
    return bool(agent and agent['active'] and task and task['status']=='In Progress' and task['owner_id']==row['actor'] and task['lock_until']>time.time() and lease(task)==row['lease'] and row['expires']>time.time() and not runtime.reason(db,row['project'],row['actor']))


def view(row):
    return {k:json.loads(row[k]) if k=='options' else row[k] for k in ('id','actor','task','kind','question','options','answer','state','created','expires')}


def operation(db,a,actor_user,now):
    ensure(db);project=a['project'];mode=a.get('action','list')
    if mode=='list':
        rows=db.execute('SELECT * FROM agent_interactions WHERE project=? ORDER BY created DESC LIMIT 50',(project,)).fetchall()
        items=[dict(view(r),state=r['state'] if r['state']!='pending' or live(db,r) else 'expired') for r in rows]
        version=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()[:24]
        if a.get('version_seen')==version:return {'changed':False,'version':version}
        return {'changed':True,'version':version,'items':items}
    author=actor_user(db,a)
    db.execute('BEGIN IMMEDIATE')
    if mode=='request':
        if author['kind']!='agent':raise ValueError('Only a task agent can request a decision')
        task=db.execute("SELECT * FROM tasks WHERE project=? AND owner_id=? AND status='In Progress' AND lock_until>? ORDER BY id LIMIT 1",(project,author['id'],time.time())).fetchone()
        if not task or (a.get('task') is not None and a['task']!=task['id']):raise ValueError('A decision requires the agent own live task claim')
        if runtime.reason(db,project,author['id']):raise ValueError('Stop/quota policy prevents starting a decision callback')
        question=a.get('question','');options=a.get('options',[]);kind=a.get('kind','question')
        if kind not in ('question','permission'):raise ValueError('Invalid interaction kind')
        if not isinstance(question,str) or not question.strip() or len(question)>12000:raise ValueError('Question must be 1..12000 characters')
        if not isinstance(options,list) or len(options)>6 or any(not isinstance(o,str) or not o.strip() or len(o)>300 for o in options) or len(set(options))!=len(options):raise ValueError('Use up to six distinct concise options')
        if kind=='permission' and options!=['Approve once','Deny','Cancel run']:raise ValueError('Permission requests offer only one-shot approval, denial or cancellation')
        if db.execute("SELECT COUNT(*) FROM agent_interactions WHERE project=? AND actor=? AND state='pending' AND expires>?",(project,author['id'],time.time())).fetchone()[0]>=3:raise ValueError('At most three pending decisions per agent')
        key=uuid.uuid4().hex
        db.execute('INSERT INTO agent_interactions(id,project,actor,task,lease,kind,question,options,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,project,author['id'],task['id'],lease(task),kind,question,json.dumps(options),now(),min(task['lock_until'],time.time()+900)))
        return view(db.execute('SELECT * FROM agent_interactions WHERE id=?',(key,)).fetchone())
    row=db.execute('SELECT * FROM agent_interactions WHERE id=? AND project=?',(a.get('id'),project)).fetchone()
    if not row:raise ValueError('Unknown project interaction')
    if mode=='get':
        if author['id']!=row['actor'] and author['kind']!='human':raise ValueError('Only the requester or a human may read this callback')
        return dict(view(row),state='expired' if row['state'] in ('pending','answered') and not live(db,row) else row['state'])
    if mode=='cancel':
        if author['id']!=row['actor'] and author['kind']!='human':raise ValueError('Only the requester or a human may cancel')
        db.execute("UPDATE agent_interactions SET state='cancelled' WHERE id=? AND state='pending'",(row['id'],))
        return {'id':row['id'],'state':'cancelled'}
    if mode!='answer':raise ValueError('Use list/request/get/answer/cancel')
    if author['kind']!='human':raise ValueError('Only a human may answer; agents cannot approve themselves')
    answer=a.get('answer','')
    if not isinstance(answer,str) or not answer.strip() or len(answer)>2000:raise ValueError('A concise answer is required')
    if row['kind']=='permission' and answer not in json.loads(row['options']):raise ValueError('Choose an offered permission decision')
    if row['state']=='answered':
        if row['answer']!=answer:raise ValueError('This decision has already been answered')
        return {'id':row['id'],'state':'answered','already_answered':True}
    if row['state']!='pending' or not live(db,row):raise ValueError('Request expired or run stopped; no permission was granted. Resume the task to request again.')
    db.execute("UPDATE agent_interactions SET answer=?,state='answered' WHERE id=?",(answer,row['id']))
    db.execute('INSERT INTO project_messages(project,actor,recipient,task,text,created) VALUES(?,?,?,?,?,?)',(project,author['id'],row['actor'],row['task'],('Decision: '+row['question'][:900]+'\nAnswer: '+answer)[:2000],now()))
    return {'id':row['id'],'state':'answered'}
