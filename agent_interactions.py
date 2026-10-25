"""Local, bounded human decisions. Never execute commands or grant permissions."""
import hashlib
import json
import time
import uuid
import runtime
import sqlite3


def ensure(db):
    # Intentionally excluded from portable snapshots: callbacks belong to a live run.
    db.execute('''CREATE TABLE IF NOT EXISTS agent_interactions(
        id TEXT PRIMARY KEY, project INTEGER, actor INTEGER, task INTEGER,
        lease TEXT, kind TEXT, question TEXT, options TEXT, answer TEXT DEFAULT '',
        state TEXT DEFAULT 'pending', created TEXT, expires REAL, metadata TEXT DEFAULT '{}')''')
    if 'metadata' not in {r[1] for r in db.execute('PRAGMA table_info(agent_interactions)')}:
        try:db.execute("ALTER TABLE agent_interactions ADD COLUMN metadata TEXT DEFAULT '{}'")
        except sqlite3.OperationalError:
            if 'metadata' not in {r[1] for r in db.execute('PRAGMA table_info(agent_interactions)')}:raise


def lease(task):
    return hashlib.sha256((task['lock_token'] or '').encode()).hexdigest()


def scope(task):
    return hashlib.sha256(json.dumps({k:task[k] for k in ('title','description','acceptance_criteria')},sort_keys=True).encode()).hexdigest()


def metadata(row):
    return json.loads(row['metadata'] or '{}')


def live(db, row):
    task=db.execute('SELECT * FROM tasks WHERE project=? AND id=?',(row['project'],row['task'])).fetchone()
    agent=db.execute('SELECT active FROM users WHERE id=?',(row['actor'],)).fetchone()
    meta=metadata(row)
    if row['kind']=='question' and meta.get('deferred'):
        return bool(agent and agent['active'] and task and task['status'] not in ('Done','Cancelled') and scope(task)==meta.get('scope') and row['expires']>time.time())
    return bool(agent and agent['active'] and task and task['status']=='In Progress' and task['owner_id']==row['actor'] and task['lock_until']>time.time() and lease(task)==row['lease'] and row['expires']>time.time() and not runtime.reason(db,row['project'],row['actor']))


def view(row):
    meta=metadata(row)
    return {k:json.loads(row[k]) if k=='options' else row[k] for k in ('id','actor','task','kind','question','options','answer','state','created','expires')} | {k:meta[k] for k in ('deferred','question_category','human_requirement') if k in meta}


def operation(db,a,actor_user,now,activity=None):
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
        policy=db.execute('SELECT autonomy_mode FROM projects WHERE id=?',(project,)).fetchone()
        deferred=kind=='question' and policy['autonomy_mode']=='Autonomous'
        meta={}
        if deferred:
            category=a.get('question_category');why=a.get('human_requirement','')
            if category not in ('missing_input','conflicting_requirements','visual_review','playtest','access'):
                raise ValueError('Autonomous mode needs a typed concrete question. Use CLI ask --input FILE with question_category, human_requirement, options and continuity instead of an unqualified input request')
            if not isinstance(why,str) or not 20<=len(why.strip())<=600:
                raise ValueError('Explain specifically why human input is indispensable (20..600 characters); resolve routine choices yourself')
            if len(options)<2:raise ValueError('Provide at least two concrete choices for the human')
            continuity=a.get('continuity',task['continuity'])
            if not isinstance(continuity,str) or len(continuity)>2000:raise ValueError('continuity must be text, max2000')
            meta={'deferred':True,'scope':scope(task),'question_category':category,'human_requirement':why.strip()}
        key=uuid.uuid4().hex
        db.execute('INSERT INTO agent_interactions(id,project,actor,task,lease,kind,question,options,created,expires,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,project,author['id'],task['id'],lease(task),kind,question,json.dumps(options),now(),time.time()+7*86400 if deferred else min(task['lock_until'],time.time()+900),json.dumps(meta)))
        if deferred:
            # Park only this owned task. The next normal supervisor launch privately
            # checkpoints its edits; no model waits/polls for a human response.
            import context_batches,telemetry
            context_batches.release(db,project,author['id'])
            db.execute('DELETE FROM file_locks WHERE task=? AND owner_id=?',(task['id'],author['id']))
            db.execute("UPDATE tasks SET status='Blocked',blocked_reason=?,blocked_reason_kind='human',lock_token=NULL,lock_until=0,continuity=?,updated=? WHERE id=?",('Awaiting human input ['+key+']: '+question[:600],continuity,now(),task['id']))
            telemetry.transition(db,task['id'],'Blocked',author['id'])
            if activity:activity(db,task['id'],author['id'],'comment','Specific human question saved in Chat; this task is parked so other ready work can continue. No acceptance or permission was granted.')
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
    resumed=False
    if metadata(row).get('deferred'):
        task=db.execute('SELECT * FROM tasks WHERE id=? AND project=?',(row['task'],project)).fetchone()
        if task['status']=='Blocked' and task['blocked_reason_kind']=='human' and ('['+row['id']+']') in task['blocked_reason'] and task['lock_until']<=time.time():
            import telemetry
            continuation=(task['continuity'][:1000]+'\nHuman input: '+answer[:800]+'\nNext: resume saved work; verify remaining acceptance normally.').strip()
            db.execute("UPDATE tasks SET status='TODO',blocked_reason='',blocked_reason_kind='external',continuity=?,updated=? WHERE id=?",(continuation,now(),task['id']))
            telemetry.transition(db,task['id'],'TODO',author['id']);resumed=True
            if activity:activity(db,task['id'],author['id'],'comment','Specific human input received; reopened for normal task/dependency/quota checks. Work is not marked Done.')
            # Only a connected enabled supervisor observes this generation. A
            # project Stop is preserved; permissions are never auto-answered.
            if not runtime.control(db,project)['paused']:
                db.execute("INSERT INTO project_control(project,paused,generation,reason,changed,actor) VALUES(?,0,1,?,?,?) ON CONFLICT(project) DO UPDATE SET generation=generation+1,reason=excluded.reason,changed=excluded.changed,actor=excluded.actor WHERE paused=0",(project,'Human input received for waiting task',time.time(),author['id']))
    return {'id':row['id'],'state':'answered',**({'task_reopened':True} if resumed else {})}
