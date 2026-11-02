"""Native Scrum CLI. Shared validation and business operations; no MCP transport."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

READS={'projects','users','task_get','task_search','task_changes','agent_status','project_metrics','coordination_metrics','session_resume','next_work','workflow_status'}
ALIASES={'ask':'agent_interaction','show':'task_get','search':'task_search','plan':'task_plan','update':'task_update','claim':'task_claim','release':'task_release','lock':'file_locks','finish':'work_finish','pause':'agent_checkpoint','status':'agent_status','metrics':'coordination_metrics','start':'work_start','projects':'projects'}


def read_operation(name,args):
    return (name in READS or (name=='file_locks' and args.get('action')=='list')
            or (name=='agent_interaction' and args.get('action','list') in ('list','get'))
            or (name=='task_attachment' and args.get('action','list') in ('list','get'))
            or (name=='project_settings' and not set(args)-{'project','actor','measurement_session'}))


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def context_file(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>2*1024*1024:raise ValueError('Invalid CLI context')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!=1:raise ValueError('Unsupported CLI context')
    return data


@contextlib.contextmanager
def locked(path):
    lock=Path(str(path)+'.lock')
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('Another command owns this context; do not run coordination writes concurrently') from None
    try:
        os.close(fd);yield
    finally:lock.unlink(missing_ok=True)


def create_context(path,project,actor,client,started,batch=False):
    task=started['task']
    state={'schema':1,'project':project,'actor':actor,'client':client,'batch':bool(batch),
           'measurement_session':started.get('measurement_session'),'current':{'id':task['id'],'code':task['code'],'token':started['token']},'receipts':[]}
    atomic(path,state);return state


def redact(value):
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if k not in ('token','lock_token')}
    return value


def invoke(name,args):
    from coordination_api import execute,specification
    specification(name) # Unknown operation names must never enter metrics.
    if name=='task_update' and args.get('status')=='Done':raise ValueError('Use finish with a live claim, not a direct Done update')
    import overhead
    started=time.perf_counter();result=None;error=None
    try:
        result=execute(name,args);return result
    except Exception as failure:
        error=failure;raise
    finally:
        # Content-free counters. Never count a second MCP transport hop.
        body=result if isinstance(result,dict) else {}
        task=args.get('id') if name.startswith('task_') or name in ('work_start','work_finish','file_locks') else None
        if name=='task_create':task=body.get('id')
        if name=='agent_interaction':task=args.get('task',body.get('task'))
        try:overhead.record(args.get('project'),name,len(json.dumps(args,separators=(',',':')).encode()),len(json.dumps(redact(result) if error is None else {'error':type(error).__name__},separators=(',',':')).encode()),(time.perf_counter()-started)*1000,error is not None,session=body.get('measurement_session',args.get('measurement_session')),task=task,actor=body.get('actor',args.get('actor')),channel='cli')
        except Exception:pass


def _advance(state,name,args,result):
    if not isinstance(result,dict):return
    if result.get('measurement_session'):state['measurement_session']=result['measurement_session']
    if name in ('work_start','task_claim'):
        claim=result.get('claim',{}) if name=='work_start' else result
        task=result.get('task',{})
        if claim.get('claimed'):
            state['current']={'id':claim['id'],'code':task.get('code') or claim.get('code'),'token':claim['token']}
    if name=='work_finish':
        following=result.get('next',{});claim=following.get('claim',{});task=following.get('task',{})
        if claim.get('claimed'):state['current']={'id':claim['id'],'code':task.get('code'),'token':claim['token']}
        elif result.get('completed',{}).get('status')=='Done':state['current']=None
    if name=='agent_interaction' and result.get('deferred'):
        state['current']=None;state['batch']=False
    if name=='task_plan' and result.get('deferred'):
        state['current']=None;state['batch']=False
    if name=='task_release' or (name=='agent_checkpoint' and args.get('action') in ('pause','offline')):state['current']=None


def coordinated(name,args,path=None):
    if not path:return redact(invoke(name,args))
    with locked(path):
        state=context_file(path)
        # Durable result precedes the context update; recover locally, never replay.
        pending=state.get('pending')
        if pending:
            receipt=Path(str(path)+'.'+pending['key']+'.receipt')
            if receipt.exists():
                recovered=json.loads(receipt.read_text(encoding='utf-8'))
                _advance(state,recovered['operation'],recovered['args'],recovered['result'])
                state.setdefault('receipts',[]).append({'key':pending['key'],'operation':recovered['operation'],'task':recovered['args'].get('id'),'result':redact(recovered['result'])})
                state['receipts']=state['receipts'][-8:];state.pop('pending',None);atomic(path,state);receipt.unlink(missing_ok=True)
            elif not read_operation(name,args) and not (name=='agent_checkpoint' and args.get('action')=='pause'):
                raise ValueError('Previous write is uncertain. Read status/show, then pause for supervisor handoff; never repeat the write blindly')
        for field in ('project','actor','client'):
            if field in args and args[field]!=state[field]:raise ValueError('CLI context '+field+' cannot be overridden')
        spec=__import__('coordination_api').specification(name)['properties']
        args=dict(args)
        for field in ('project','actor','client','measurement_session'):
            if field in spec and state.get(field) is not None:args.setdefault(field,state[field])
        current=state.get('current') or {}
        if name=='agent_interaction' and args.get('action')=='request' and not current:
            raise ValueError('ask requires the current claimed task')
        if name=='work_finish':
            for old in reversed(state.get('receipts',[])):
                if old['operation']==name and old['task']==args.get('id'):
                    return {'completed':old['result'].get('completed'),'replayed':True,'current_task':{k:v for k,v in current.items() if k!='token'},'instruction':'Completion was already committed. Reconcile current scope with show before continuing; no write was replayed.'}
        needs_token=(name in ('work_finish','task_update','task_release','file_locks')
                     or name=='task_attachment' and args.get('action') in ('add','remove'))
        if name=='task_plan' and 'defer_task' in args:
            if not current or args['defer_task']!=current['id']:raise ValueError('defer_task must be the current claimed task')
            if args.get('token') not in (None,current['token']):raise ValueError('Claim token cannot be overridden')
            args['token']=current['token']
        if needs_token and current and args.get('id')==current['id']:
            if args.get('token') not in (None,current['token']):raise ValueError('Claim token cannot be overridden')
            args['token']=current['token']
        if name=='work_finish':
            if not current or args.get('id')!=current['id']:raise ValueError('finish requires the explicitly named current claimed task')
            if state['batch']:args.update(use_batch=True,claim_next=True,client=state['client'])
            else:args.update(close_session=True,client=state['client'])
        if name=='task_update' and args.get('status')=='Done':raise ValueError('Use finish with the current claim; direct Done updates are not supported by the worker CLI')
        from coordination_api import specification
        from mcp_server import _validate
        _validate(args,specification(name))
        mutation=not read_operation(name,args)
        key=uuid.uuid4().hex
        if mutation:state['pending']={'key':key,'operation':name,'task':args.get('id')};atomic(path,state)
        try:result=invoke(name,args)
        except ValueError as error:
            # This API is a single SQLite transaction with no post-commit Git
            # jobs. A rejected question is known not to have been committed.
            from planning import PlanRejected
            from git_completion import RecoveryIncomplete
            if mutation and (name=='agent_interaction' or isinstance(error,(PlanRejected,RecoveryIncomplete))):
                state.pop('pending',None);atomic(path,state)
            raise
        if mutation:
            receipt=Path(str(path)+'.'+key+'.receipt')
            atomic(receipt,{'operation':name,'args':args,'result':result})
            _advance(state,name,args,result)
            state.setdefault('receipts',[]).append({'key':key,'operation':name,'task':args.get('id'),'result':redact(result)})
            state['receipts']=state['receipts'][-8:];state.pop('pending',None);atomic(path,state);receipt.unlink(missing_ok=True)
        return redact(result)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=[*ALIASES,'call','help'])
    p.add_argument('operation',nargs='?',help='Exact API operation for call/help')
    p.add_argument('--context',default=os.environ.get('SCRUM_CLI_CONTEXT'))
    p.add_argument('--project',help='Project ID, UUID or checkout path; defaults to cwd outside managed runs')
    p.add_argument('--actor',type=int);p.add_argument('--client',choices=['codex','claude'])
    p.add_argument('--task',help='Explicit task ID/code; required for finish and task mutations')
    p.add_argument('--input',type=Path,help='UTF-8 JSON arguments (or task-plan items array)')
    p.add_argument('--stdin',action='store_true',help='Read JSON arguments from stdin, never shell-built JSON')
    for field in ('evidence','continuity','reason','query','status','file'):p.add_argument('--'+field)
    for field in ('evidence','continuity'):p.add_argument('--'+field+'-file',type=Path)
    p.add_argument('--decisions-file',type=Path)
    p.add_argument('--paths',nargs='+');p.add_argument('--action');p.add_argument('--limit',type=int);p.add_argument('--after',type=int)
    return p


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    p=parser();ns=p.parse_args(argv)
    try:
        name=ns.operation if ns.command in ('call','help') else ALIASES[ns.command]
        if not name:raise ValueError('An exact operation is required')
        from coordination_api import specification
        spec=specification(name)
        if ns.command=='help':print(json.dumps({'operation':name,'arguments':spec},separators=(',',':')));return 0
        raw=ns.input.read_text(encoding='utf-8-sig') if ns.input else sys.stdin.read() if ns.stdin else '{}'
        if len(raw)>1024*1024:raise ValueError('Input too large')
        args=json.loads(raw)
        if ns.command=='plan' and isinstance(args,list):args={'items':args}
        if not isinstance(args,dict):raise ValueError('Arguments must be a JSON object')
        bootstrap=bool(ns.context and not Path(ns.context).exists() and ns.command=='start')
        state=context_file(ns.context) if ns.context and not bootstrap else {}
        if ns.project:
            if ns.project.isdecimal():project=int(ns.project)
            else:
                projects=invoke('projects',{});value=Path(ns.project).resolve()
                matches=[x for x in projects if x.get('uuid')==ns.project or Path(x.get('path','')).resolve()==value]
                if len(matches)!=1:raise ValueError('Project path/UUID must resolve uniquely')
                project=matches[0]['id']
            args['project']=project
        elif 'project' in spec['properties'] and not state and 'project' not in args:
            projects=invoke('projects',{});matches=[x for x in projects if Path(x.get('path','')).resolve()==Path.cwd().resolve()]
            if len(matches)!=1:raise ValueError('Supply --project or a managed --context')
            args['project']=matches[0]['id']
        for field in ('actor','client','query','status','limit','after','file','action'):
            value=getattr(ns,field)
            if value is not None:args[field]=value
        for field in ('evidence','continuity'):
            value=getattr(ns,field);file=getattr(ns,field+'_file')
            if file:
                value=file.read_text(encoding='utf-8-sig')
                if len(value)>16000:raise ValueError('Use compact evidence/continuity, not a full log')
                if field=='evidence' and value.lstrip().startswith('{'):
                    report=json.loads(value);value=report.get('evidence')
                    if not isinstance(value,str):raise ValueError('JSON evidence file requires an evidence string')
            if value is not None:args[field]=value
        if ns.reason is not None:args['reason' if name=='agent_checkpoint' else 'blocked_reason']=ns.reason
        if ns.decisions_file:
            if ns.decisions_file.stat().st_size>32000:raise ValueError('Decision file too large')
            args['decisions']=json.loads(ns.decisions_file.read_text(encoding='utf-8-sig'))
        if ns.paths is not None:args['additional_files' if name=='work_finish' else 'paths']=ns.paths
        if ns.command=='ask':
            args.setdefault('action','request');args.setdefault('kind','question')
            if state.get('current'):args.setdefault('task',state['current']['id'])
        if ns.command=='pause':args['action']='pause'
        if ns.command=='start' and ns.task:args['claim']=True
        if ns.command=='lock':args.setdefault('action','acquire')
        if ns.task:
            current=state.get('current') or {}
            if ns.task.isdecimal():ident=int(ns.task)
            elif ns.task==current.get('code'):ident=current['id']
            else:
                task=coordinated('task_get',{'code':ns.task,**({'project':args['project']} if 'project' in args else {})},ns.context if not bootstrap else None);ident=task['id']
            args['task' if name=='agent_interaction' and args.get('action')=='request' else 'id']=ident
        elif name in ('work_finish','task_update','task_claim','task_release') and 'id' not in args:raise ValueError('Name --task explicitly')
        elif name=='task_get' and not any(k in args for k in ('id','code')) and state.get('current'):args['id']=state['current']['id']
        if name in ('work_start','task_claim','work_finish') and not ns.context:raise ValueError('Use --context FILE to keep claim tokens private and durable')
        if bootstrap:
            if not all(k in args for k in ('project','actor','client')):raise ValueError('start requires project, actor and client')
            atomic(ns.context,{'schema':1,'project':args['project'],'actor':args['actor'],'client':args['client'],'current':None,'batch':False,'receipts':[]})
        result=coordinated(name,args,ns.context)
        print(json.dumps(result,separators=(',',':'),ensure_ascii=False));return 0
    except Exception as error:
        if isinstance(error,ValueError):
            from planning import PlanRejected
            from git_completion import RecoveryIncomplete
            rejected=isinstance(error,PlanRejected)
            recovery=isinstance(error,RecoveryIncomplete)
            detail={'code':'PLAN_REJECTED' if rejected else 'RECOVERY_INCOMPLETE' if recovery else 'VALIDATION_ERROR','message':str(error)[:1000]}
            if rejected:detail.update(committed=False,next_step='Correct this rejected plan; the transaction was rolled back. Do not pause solely for this validation rejection.')
            if recovery:detail.update(committed=False,next_step='The completion was rejected before transition. Preserve the current claim; commit and publish only the original verified scope before finishing recovery.')
            print(json.dumps({'error':detail,'retried':False},separators=(',',':')),file=sys.stderr)
            return 1
        from diagnostics import failure
        detail=failure(error,locals().get('name','unknown'))
        print(json.dumps({'error':detail,'retried':False,'next_step':'Inspect recorded state before retrying a write. Context uncertainty requires status/show then pause or supervisor reconciliation.'},separators=(',',':')),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
