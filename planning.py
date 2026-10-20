"""Atomic bounded task planning, without model calls or implicit claims."""
import json
import time
import coordination as coord
import runtime
import telemetry


def apply(db,a,project):
    from store import actor_user,required,priority,acceptance_criteria,dependencies,now,activity,project_prefix,duplicate_task
    actor=actor_user(db,a)
    db.execute('BEGIN IMMEDIATE')
    runtime.gate(db,project,actor['id'])
    items=a.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=10:
        raise ValueError('Plan needs 1..10 concrete outcomes')
    allowed={'id','key','title','description','acceptance_criteria','priority','category','files','dependencies','scope_size','scope_reason','model_tier','model_reason','verification_level','verification_reason','context_group'}
    prepared=[];keys={};existing=set()
    for item in items:
        if not isinstance(item,dict) or set(item)-allowed:
            raise ValueError('Unsupported planning fields; planning never changes state or ownership')
        key=required(item.get('key'),'key',80)
        if key in keys:raise ValueError('Plan keys must be unique')
        keys[key]=None
        old=None
        if 'id' in item:
            if type(item['id']) is not int or item['id'] in existing:raise ValueError('Existing task IDs must be unique integers')
            existing.add(item['id'])
            old=db.execute('SELECT * FROM tasks WHERE id=? AND project=?',(item['id'],project)).fetchone()
            if not old:raise ValueError('Existing task must belong to this project')
            if old['status'] in ('Done','Cancelled') or old['lock_until']>time.time():
                raise ValueError('Plan only unfinished tasks without an active claim; preserve current work')
        title=required(item.get('title'),'title',240)
        same=None if old else duplicate_task(db,project,title,a)
        if same:
            # Reused outcomes still reject invalid planning input.
            from store import validate_verification
            import model_routing
            validate_verification(item.get('verification_level','auto'),item.get('verification_reason',''))
            model_routing.validate('',item.get('model_tier','auto'),item.get('model_reason',''))
        description=required(item.get('description'),'description',12000)
        criteria=acceptance_criteria(item.get('acceptance_criteria'))
        size=item.get('scope_size',old['scope_size'] if old else 'Unknown')
        reason=item.get('scope_reason',old['scope_reason'] if old else '')
        coord.validate_scope(size,reason)
        files=coord.file_plan(item.get('files',json.loads(old['files']) if old else []))
        deps=item.get('dependencies',json.loads(old['dependencies']) if old else [])
        if not isinstance(deps,list) or len(deps)>100 or any(type(d) not in (str,int) for d in deps):
            raise ValueError('Dependencies must be task IDs or plan keys')
        prepared.append(dict(same=same,item=item,old=old,key=key,title=title,description=description,criteria=criteria,size=size,reason=reason,files=files,deps=deps))
    prefix=project_prefix(db.execute('SELECT name FROM projects WHERE id=?',(project,)).fetchone()['name'])
    result=[]
    resolved_items=[]
    for value in prepared:
        old=value['old'];item=value['item']
        if value['same']:
            # Resumed sessions often re-plan known work: reuse, never repeat it.
            same=value['same'];keys[value['key']]=same['id']
            result.append({'key':value['key'],'id':same['id'],'code':f"{prefix}-{same['number']}",'status':same['status'],'existing':True})
            continue
        rank=priority(item.get('priority',old['priority'] if old else 2))
        category=required(item.get('category',old['category'] if old else 'General'),'category',80)
        if old:
            task=old['id'];number=old['number'];status=old['status']
            db.execute('UPDATE tasks SET title=?,description=?,acceptance_criteria=?,priority=?,category=?,files=?,scope_size=?,scope_reason=?,updated=? WHERE id=?',
                       (value['title'],value['description'],json.dumps(value['criteria']),rank,category,json.dumps(value['files']),value['size'],value['reason'],now(),task))
            activity(db,task,actor['id'],'work','Planning refined the bounded outcome; previous title: '+old['title']+'. Existing history, state and evidence preserved.')
        else:
            number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM tasks WHERE project=?',(project,)).fetchone()[0]
            task=db.execute('INSERT INTO tasks(project,title,description,acceptance_criteria,priority,category,files,scope_size,scope_reason,number,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (project,value['title'],value['description'],json.dumps(value['criteria']),rank,category,json.dumps(value['files']),value['size'],value['reason'],number,now())).lastrowid
            status='TODO'
            telemetry.transition(db,task,status,actor['id'],source='created')
            activity(db,task,actor['id'],'created','Task planned in TODO; execution remains unclaimed.')
        import model_routing
        tier=item.get('model_tier',old['model_tier'] if old else 'auto')
        model_reason=item.get('model_reason',old['model_reason'] if old else '')
        model_routing.validate('',tier,model_reason)
        if old and old['model'] and actor['kind']!='human' and any(k in item for k in ('model_tier','model_reason')):raise ValueError('Human task model is fixed')
        db.execute('UPDATE tasks SET model_tier=?,model_reason=? WHERE id=?',(tier,model_reason,task))
        from store import validate_context_group
        group=validate_context_group(item.get('context_group',old['context_group'] if old else ''))
        db.execute('UPDATE tasks SET context_group=? WHERE id=?',(group,task))
        from store import validate_verification
        level=item.get('verification_level',old['verification_level'] if old else 'auto')
        reason=item.get('verification_reason',old['verification_reason'] if old else '')
        validate_verification(level,reason)
        db.execute('UPDATE tasks SET verification_level=?,verification_reason=? WHERE id=?',(level,reason,task))
        keys[value['key']]=task
        result.append({'key':value['key'],'id':task,'code':f'{prefix}-{number}','status':status})
    for value in prepared:
        if value['same']:continue
        resolved=[]
        for dep in value['deps']:
            if isinstance(dep,str):
                if dep not in keys:raise ValueError('Unknown dependency plan key')
                dep=keys[dep]
            resolved.append(dep)
        task=keys[value['key']]
        previous=set(json.loads(value['old']['dependencies'])) if value['old'] else set()
        for dep in resolved:
            row=db.execute('SELECT status FROM tasks WHERE id=? AND project=?',(dep,project)).fetchone()
            if not row:raise ValueError('Dependency must belong to this project')
            if row['status']=='Cancelled' and dep not in previous:raise ValueError('Cannot add a cancelled prerequisite')
        resolved_items.append((task,resolved))
    for task,resolved in resolved_items:
        db.execute('UPDATE tasks SET dependencies=? WHERE id=?',(json.dumps(resolved),task))
    for task,resolved in resolved_items:
        dependencies(db,project,task,resolved) # Validate the final graph, not intermediate edits.
    return {'items':result}
