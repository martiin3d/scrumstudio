"""Bounded compound operations; existing operations retain their safety checks."""

def compact(value):
    if isinstance(value, list):
        return [compact(v) for v in value]
    if not isinstance(value, dict):
        return value
    if all(k in value for k in ('id','code','version')):
        return value # Execution context includes meaningful empty fields.
    # Omit empty fields, never false/zero (these can be meaningful).
    return {k: compact(v) for k, v in value.items()
            if k != 'instruction' and v is not None and v != '' and v != []}


def operation(action, a, call):
    project = a['project']
    seen = {k: a[k] for k in ('retro_seen', 'settings_seen','task_seen') if k in a}
    if action == 'work_start':
        actor = a.get('actor')
        if actor is None:
            if not a.get('identity') or not a.get('name'):
                raise ValueError('Supply actor or persistent identity and name')
            actor = call('user_register', {k: a[k] for k in ('identity', 'name')} | {'kind': 'agent'})['id']
        base = {'project': project, 'actor': actor}
        resume = call('session_resume', base | {k: a[k] for k in ('after', 'limit') if k in a})
        report = call('agent_checkpoint', base | {'client': a['client']} |
                      {k: a[k] for k in ('observed_at', 'session_used_percent', 'weekly_used_percent', 'session_resets_at', 'weekly_resets_at') if k in a})
        if resume['settings']['settings_version'] == a.get('settings_seen'):
            resume.pop('settings')
        result = {'actor': actor, **resume, 'checkpoint': report}
        result['workflow'] = call('workflow_status', {'project':project, **({'id':a['id']} if 'id' in a else {})})
        import overhead
        try:
            result['measurement_session']=overhead.start_session(project,actor,result['workflow']['effective'],a.get('measurement_session'))
        except (OSError,ValueError,overhead.sqlite3.Error):
            result['measurement_unavailable']=True
        # Never claim or release files implicitly; the worker must review them.
        if not report['should_stop'] and not resume['own_file_locks']['has_more']:
            if a.get('id') is not None:
                result['next']={'task':call('task_get',{'project':project,'id':a['id'],**({'version_seen':a['task_seen']} if a.get('task_seen') else {})})}
            else:
                result['next'] = call('next_work', base | seen | {'settings_seen': a.get('settings_seen', -1)})
            result['next'].pop('settings', None) # already returned by resume
            if a.get('claim', False):
                pending = resume['own_file_locks'].get('items', [])
                if result['workflow']['effective']!='Light':
                    result['claim_deferred']='Standard workflow required: '+result['workflow']['reason']
                elif pending:
                    result['claim_deferred']='Review own locks before claiming'
                elif a.get('id') is None:
                    result['claim_deferred']='Explicit task id required; no automatic candidate claim'
                else:
                    candidate=result['next']['task']
                    try:
                        result['claim']=call('task_claim',base | {'id':a['id'],'require_light':True,'retro_seen':a.get('retro_seen')} | {k:a[k] for k in ('files','token') if k in a})
                        result['task']=call('task_get',{'project':project,'id':a['id']}) if result['claim'].get('claimed') else candidate
                        if result['claim'].get('claimed'):
                            result['task']['status']='In Progress'
                        else:
                            result['workflow']['effective']='Standard'
                            result['workflow']['reason']='File reservation conflict'
                    except ValueError as error:
                        result['claim_deferred']=str(error)
                    result.pop('next',None)
    else:
        base = {'project': project, 'actor': a['actor']}
        if a.get('use_batch') and a.get('client') not in ('codex','claude'):raise ValueError('Batch completion requires client')
        if a.get('close_session'):
            items=a.get('retrospective_items',[])
            if a.get('client') not in ['codex','claude'] or not isinstance(items,list) or len(items)>100 or any(not isinstance(v,str) or not v.strip() or len(v)>4000 for v in items):
                raise ValueError('Session close requires client and valid optional retrospective_items')
            review=a.get('review_items',[])
            if not isinstance(review,list) or len(review)>100 or any(not isinstance(v,str) or not v.strip() or len(v)>4000 for v in review):
                raise ValueError('Invalid review_items')
            own=call('session_resume',base)
            if own['active_tasks']>1 or own['own_file_locks']['has_more'] or any(lock['task']!=a['id'] for lock in own['own_file_locks'].get('items',[])):
                raise ValueError('Other own work/reservations require explicit handoff before closing')
        update = base | {k: a[k] for k in ('id', 'token', 'evidence', 'checklist', 'log','continuity','usage_report') if k in a}
        update['status'] = 'Done'
        completed = call('task_update', update)
        # Completion commits before selection. A later selection failure never
        # rolls back verified delivery or asks the worker to complete it again.
        result = {'completed': completed}
        try:
            if a.get('close_session'):
                if items:
                    result['retrospective']=call('note_add',{'project':project,'task':a['id'],'kind':'Retrospective','title':'Session retrospective','items':items})
                if a.get('review_items'):
                    result['review']=call('note_add',{'project':project,'task':a['id'],'kind':'Review','title':'Session handoff','items':a['review_items']})
                result['session']=call('agent_checkpoint',base | {'client':a['client'],'action':'offline','summary':'Verified task completed.'})
            elif a.get('use_batch'):
                result['next']=call('batch_next',base|{'id':a['id']})
                if result['next'].get('batch_done'):
                    result['session']=call('agent_checkpoint',base|{'client':a['client'],'action':'offline'})
            else:
                result['next'] = call('next_work', base | seen)
        except (ValueError, OSError) as error:
            result['close_error' if a.get('close_session') else 'next_error'] = str(error)
    return result if a.get('verbose', False) else decision_payload(result)


def decision_payload(result):
    """Drop redundant diagnostics, never drop stop signals or conflicts."""
    result=compact(result)
    for key in ('checkpoint','session'):
        report=result.get(key)
        if report:
            control=report.get('control',{})
            if not control.get('paused'):report.pop('control',None)
            if report.get('tasks_released')==0:report.pop('tasks_released',None)
    locks=result.get('own_file_locks')
    if locks and not locks.get('items') and not locks.get('has_more'):
        locks.pop('next_cursor',None)
    following=result.get('next')
    if following is not None:
        for key in ('file_conflicts_skipped','missing_workspace_skipped','invalid_files_skipped','blocked_total'):
            if following.get(key)==0:following.pop(key,None)
        if not following.get('task') and not following.get('should_stop'):
            following['no_candidate']=True
        if following.get('capacity',{}).get('active')==result.get('active_tasks'):
            result.pop('active_tasks',None)
    return result
