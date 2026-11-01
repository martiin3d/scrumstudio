"""Small durable intent records; discovery is native and limited to task scope."""
import json
import re
from pathlib import PurePosixPath

def paths(values):
    if not isinstance(values,list) or not 1<=len(values)<=20:raise ValueError('Decision scope requires 1..20 relative files or directory/** paths')
    result=[]
    for value in values:
        if not isinstance(value,str) or not value.strip() or len(value)>240:raise ValueError('Invalid decision file scope')
        value=value.replace('\\','/').strip();base=value[:-3] if value.endswith('/**') else value
        if PurePosixPath(base).is_absolute() or ':' in base or any(x in ('..','.','') for x in base.split('/')) or any(x in base for x in '*?[]\x00'):raise ValueError('Use relative literal file or directory/** decision scope')
        result.append(value)
    return list(dict.fromkeys(result))

def validate(items):
    if not isinstance(items,list) or len(items)>5:raise ValueError('At most five concise decisions per task')
    seen=set();result=[]
    for item in items:
        if not isinstance(item,dict) or set(item)-{'key','decision','reason','files','supersedes'}:raise ValueError('Invalid decision fields')
        key=item.get('key','')
        if not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9._-]{0,63}',key) or key in seen:raise ValueError('Decision key must be unique within task')
        seen.add(key)
        for field,limit in [('decision',600),('reason',300)]:
            if not isinstance(item.get(field),str) or not item[field].strip() or len(item[field])>limit:raise ValueError('A concise decision and reason are required')
        supersedes=item.get('supersedes',[])
        if not isinstance(supersedes,list) or len(supersedes)>10 or any(not isinstance(x,str) or not re.fullmatch('[1-9][0-9]*:[a-z][a-z0-9._-]{0,63}',x) for x in supersedes):raise ValueError('Supersedes must reference task-number:key records')
        if len(json.dumps(item,ensure_ascii=False).encode('utf-8'))>5000:raise ValueError('Keep each scoped decision under 5000 bytes')
        result.append({'key':key,'decision':item['decision'].strip(),'reason':item['reason'].strip(),'files':paths(item.get('files')),'supersedes':list(dict.fromkeys(supersedes))})
    return result

def records(db,project,prefix):
    result=[]
    for row in db.execute("SELECT id,number,status,decisions FROM tasks WHERE project=? AND decisions!='[]' ORDER BY number",(project,)):
        for item in json.loads(row['decisions']):result.append({**item,'ref':str(row['number'])+':'+item['key'],'source_task':row['id'],'source_code':prefix+'-'+str(row['number']),'source_status':row['status']})
    return result

def active(db,project,prefix):
    all_records=records(db,project,prefix);by_ref={x['ref']:x for x in all_records};hidden=set()
    def hide(ref,visited):
        pending=[ref]
        while pending:
            current=pending.pop()
            if current in visited:continue
            visited.add(current);hidden.add(current)
            pending.extend(by_ref.get(current,{}).get('supersedes',[]))
    for item in all_records:
        if item['source_status']=='Done':
            for ref in item['supersedes']:hide(ref,set())
    return [x for x in all_records if x['source_status']=='Done' and x['ref'] not in hidden]

def save(db,project,task,items,prefix):
    items=validate(items)
    source=db.execute('SELECT status,decisions FROM tasks WHERE project=? AND id=?',(project,task)).fetchone()
    if source['status']=='Done' and json.loads(source['decisions']) and json.loads(source['decisions'])!=items:raise ValueError('Delivered decisions are immutable; record a replacement on a new task')
    known={x['ref']:x for x in records(db,project,prefix)};current=active(db,project,prefix)
    for item in items:
        for ref in item['supersedes']:
            prior=known.get(ref)
            if not prior or prior['source_task']==task or prior['source_status']!='Done' or prior['key']!=item['key']:raise ValueError('Replacement must reference a completed decision with the same key')
        others={x['ref'] for x in current if x['key']==item['key'] and x['source_task']!=task}
        if not others.issubset(set(item['supersedes'])):raise ValueError('Explicitly supersede current decisions: '+', '.join(sorted(others)))
    db.execute('UPDATE tasks SET decisions=? WHERE project=? AND id=?',(json.dumps(items),project,task))

def overlaps(a,b):
    a=a.casefold();b=b.casefold()
    if a==b:return True
    return (a.endswith('/**') and (b==a[:-3] or b.startswith(a[:-2]))) or (b.endswith('/**') and (a==b[:-3] or a.startswith(b[:-2])))

def context(db,project,task,prefix,args):
    scope=args.get('decision_files',task['files'])
    if not scope:return None
    if 'decision_files' in args:scope=paths(scope)
    after=args.get('decisions_after',0);limit=args.get('decisions_limit',5)
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=10:raise ValueError('Invalid decisions page')
    items=[x for x in active(db,project,prefix) if x['source_task']!=task['id'] and any(overlaps(a,b) for a in x['files'] for b in scope)]
    if not items:return None
    selected=[];size=0
    for item in items[after:after+limit]:
        size+=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))
        if selected and size>6000:break
        selected.append(item)
    keys=[x['key'] for x in items];conflicts=sorted({key for key in keys if keys.count(key)>1})
    result={'items':selected,'total':len(items),'has_more':after+len(selected)<len(items),'next_after':after+len(selected)}
    if conflicts:result['conflicting_keys']=conflicts
    return result
