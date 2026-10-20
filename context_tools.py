"""Small task context, conditional reads and bounded discovery."""
import hashlib
import json


def reminder_hint(db,project,task):
    if task['status'] in ('Done','Cancelled'):return task
    ids=[r[0] for r in db.execute("SELECT id FROM notes WHERE project=? AND task=? AND audience='agent' AND done=0 ORDER BY id",(project,task['id']))]
    if ids:
        task['pending_ai_notes']=len(ids)
        task['version']=digest({'task':task['version'],'pending_ai_notes':ids})
    return task


def linked_notes(db,project,task,args):
    limit=args.get('notes_limit',5);after=args.get('notes_after',0)
    if type(limit) is not int or not 1<=limit<=20 or type(after) is not int or after<0:raise ValueError('Invalid linked notes cursor/limit')
    audience=args.get('notes_audience','agent')
    if audience not in ('agent','human','all'):raise ValueError('Invalid notes_audience')
    scope=" AND audience='agent' AND done=0 AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=notes.task AND tasks.status NOT IN ('Done','Cancelled'))" if audience=='agent' else " AND audience='human'" if audience=='human' else ''
    rows=db.execute('SELECT id,kind,title,done,created,items,audience FROM notes WHERE project=? AND task=? AND id>?'+scope+' ORDER BY id LIMIT ?',(project,task,after,limit+1)).fetchall()
    items=[]
    for row in rows[:limit]:
        item={k:row[k] for k in ('id','kind','title','done','created','audience')}
        if args.get('notes_content'):item['items']=json.loads(row['items'])
        items.append(item)
    return {'items':items,'has_more':len(rows)>limit,'next_cursor':items[-1]['id'] if items else after}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24]


def basis(task):
    content={k:task.get(k) for k in ('title','description','acceptance_criteria','status','dependencies','blocked_reason','files','checklist','evidence','attachments')}
    if task.get('blocked_reason') and task.get('blocked_reason_kind')=='dependency':content['blocked_reason_kind']='dependency'
    return digest(content)


def decorate(task):
    task['continuity_stale']=bool(task.get('continuity') and task.get('continuity_basis')!=basis(task))
    task.pop('continuity_basis',None)
    task['version']=digest({k:v for k,v in task.items() if k not in ('uid','version')})
    return task


def conditional(task, seen=None):
    if seen and seen==task['version']:
        return {k:task[k] for k in ('id','code','version')} | {'changed':False}
    return task


def agent_task(task):
    """Human presentation metadata is available only via full=true/snapshot."""
    source_version=task.get('version')
    result={k:v for k,v in task.items() if k not in ('project','number','uid','updated','created')}
    if not result.get('blocked_reason'):result.pop('blocked_reason_kind',None)
    result['attachment_count']=len(result.pop('attachments',[]))
    for key in ('model_selection','scope_reason','model_reason','context_group'):
        if not result.get(key):result.pop(key,None)
    if result.get('verification_level','auto')=='auto' and not result.get('verification_reason'):
        result.pop('verification_level',None);result.pop('verification_reason',None)
    if result.get('model_selection'):
        result['model_selection']={k:v for k,v in result['model_selection'].items() if k in ('model','source','tier')}
    # Keep hidden source metadata (notably attachment reference identities) in
    # the opaque compact version without returning that metadata to callers.
    result['version']=digest({'source_version':source_version,'view':{k:v for k,v in result.items() if k!='version'}})
    return result


def search(db, project, prefix, args):
    limit=args.get('limit',20);after=args.get('after',0)
    if type(limit) is not int or not 1<=limit<=50 or type(after) is not int or after<0:
        raise ValueError('limit must be 1..50; after must be a nonnegative id')
    clauses=['project=?','id>?'];values=[project,after]
    def literal(value):
        if not isinstance(value,str) or len(value)>200:
            raise ValueError('Search text must be at most 200 characters')
        return '%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    if args.get('query'):
        term=literal(args['query']);clauses.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR continuity LIKE ? ESCAPE '\\')");values.extend([term]*3)
    if args.get('file'):
        clauses.append("EXISTS (SELECT 1 FROM json_each(tasks.files) WHERE value LIKE ? ESCAPE '\\')");values.append(literal(args['file']))
    if args.get('status'):
        from store import STATUSES
        if args['status'] not in STATUSES:raise ValueError('Invalid status')
        clauses.append('status=?');values.append(args['status'])
    if args.get('code'):
        code=args['code']
        if not isinstance(code,str) or not code.startswith(prefix+'-') or not code[len(prefix)+1:].isdigit():raise ValueError('Invalid project task code')
        clauses.append('number=?');values.append(int(code[len(prefix)+1:]))
    rows=db.execute('SELECT * FROM tasks WHERE '+' AND '.join(clauses)+' ORDER BY id LIMIT ?',values+[limit+1]).fetchall()
    from store import task_dict
    items=[]
    for row in rows[:limit]:
        task=task_dict(row,prefix)
        items.append({k:task[k] for k in ('id','code','title','status','priority','version','continuity_stale')})
    return {'items':items,'has_more':len(rows)>limit,'next_cursor':items[-1]['id'] if items else after}


def selection_header(task):
    """Navigation only; execution scope is loaded explicitly for the selected id."""
    result={k:task[k] for k in ('id','code','title','status','priority','dependencies','locked','owner_id','continuity_stale','pending_ai_notes') if k in task}
    result['version']=digest({'view':'selection','task_version':task['version']})
    return result
