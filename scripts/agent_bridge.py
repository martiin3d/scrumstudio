"""Opt-in local CLI bridge: OS polling costs no model tokens.

Run explicitly with --allow-run. Wakeup starts only this bridge's CLI worker;
Stop interrupts only its process group, then records an automatic handoff.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from mcp_client import ScrumClient
from provider_usage import codex_usage, usage_file
from cli_runtime import codex_executable,model_name,worker_python

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import execution_usage
import idle_signals


def continue_after_task(task, batch_run, released):
    # A real impediment releases this task; another eligible task may proceed.
    # Ordinary chaining never retries In Progress; closure recovery is bounded
    # separately and targets only the same unfinished task.
    if task.get('status') in ('Blocked','Cancelled'):return True
    return bool(released.get('batch_complete')) if batch_run else task.get('status')=='Done'


def unfinished_owned(task,actor):
    return task.get('owner_id')==actor and (task.get('status')=='In Progress' or
        (task.get('status')=='TODO' and bool(task.get('continuity')) and not task.get('blocked_reason')))


def recover_incomplete(task,actor,attempts,report,exit_code):
    """One bounded closure recovery; never infer completion from exit code 0."""
    return (exit_code==0 and unfinished_owned(task,actor)
            and attempts.get(task.get('id'),0)<1 and not report.get('should_stop')
            and not report.get('control',{}).get('paused') and report.get('profile_enabled',True))


def queue_checkout_recovery(rpc,project,actor,offer,attempted):
    issue=offer.get('checkout_recovery')
    if not issue:return False
    fingerprint=json.dumps(issue,sort_keys=True)
    if fingerprint in attempted:return False
    attempted.add(fingerprint)  # An uncertain write must not cause a retry loop.
    branch=str(issue['branch'])
    result=rpc.call('task_plan',{'project':project,'actor':actor,'items':[{
        'key':'checkout-recovery','title':('Recover checkout for available work: '+branch)[:185]+' '+hashlib.sha256(fingerprint.encode()).hexdigest()[:8],
        'description':'The human enabled autonomous recovery tasks. Ready authorized work cannot start because of checkout/preparation state on '+branch+'. Inspect saved task continuity and current Git state, reserve discovered files, and recover branch availability without losing work. Preserve unrelated edits/staging; no blanket commit, discard, stash or force push. Commit/publish only verified related outcomes under the recorded Git policy. If a completed prerequisite is not integrated, reconcile its recorded parent chain and publish the verified commits needed by the waiting task; unfinished context-group work must stay unfinished. If recovery needs an external action, record a precise blocker and exit; never repeat an unchanged attempt. Concrete error: '+str(issue.get('error','Uncommitted work prevents switching to another ready task.'))[:700]+'. Diagnostic paths: '+json.dumps(issue.get('files',[])[:20]),
        'acceptance_criteria':['Ready work can proceed without losing partial/unrelated work, or a concrete external blocker is recorded with resumable continuity.'],
        'category':'Git recovery','priority':1,'scope_size':'Small','scope_reason':'One concrete checkout recovery; no new feature.',
        'model_tier':'standard','model_reason':'Scoped Git state recovery.'}]})
    recovery=next((item for item in result.get('items',[]) if item.get('status','TODO')=='TODO'),None)
    if not recovery:return False
    if offer.get('id'):
        waiting=rpc.call('task_get',{'project':project,'id':offer['id']})
        rpc.call('task_update',{'project':project,'id':waiting['id'],'actor':actor,'status':'Blocked',
            'dependencies':list(dict.fromkeys([*waiting['dependencies'],recovery['id']])),
            'blocked_reason':'Waiting for scoped Git preparation recovery','blocked_reason_kind':'dependency'})
    return recovery['id']


def interactive_command(cmd,client,project,actor,folder,model,policy):
    if client!='codex' or 'exec' not in cmd:return cmd
    split=cmd.index('exec')
    reviewer='user' if policy.get('agent_approval_mode')=='Ask in chat' else 'auto_review'
    server=cmd[:split]
    # Replace only our per-run reviewer override; organization requirements win.
    server=[('approvals_reviewer='+json.dumps(reviewer)) if arg.startswith('approvals_reviewer=') else arg for arg in server]
    full=policy.get('agent_approval_mode')=='Full access'
    if full:
        server=[('approval_policy="never"' if arg.startswith('approval_policy=') else 'default_permissions=":danger-full-access"' if arg.startswith('default_permissions=') else arg) for arg in server]
    return [sys.executable,str(ROOT/'scripts/codex_interactive.py'),'--project',str(project),'--actor',str(actor),'--cwd',str(folder),'--signals',str(policy.get('workspace_root') or folder),'--reviewer',reviewer,*(['--full-access'] if full else []),*(['--model',model] if model else []),'--',*server,'app-server']


def worker_filesystem(folder):
    home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
    rules={p.as_posix():'read' for p in (home/'skills',Path.home()/'.agents/skills',Path(worker_python()).resolve().parent)}
    # Unity's startup writes its shared HTTP cache even for headless tests.
    # Do not expose all AppData or grant write access to tool installations.
    root=Path(folder)
    try:
        unity=(root/'ProjectSettings/ProjectVersion.txt').is_file() or any((p/'ProjectSettings/ProjectVersion.txt').is_file() for p in root.iterdir() if p.is_dir() and not p.name.startswith('.'))
    except OSError:unity=False
    if sys.platform=='win32' and unity:
        local=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))
        if local.is_absolute():rules[(local/'Unity/Caches').as_posix()]='write'
    return rules

# Only the board workflow authorized by Start/Wakeup; never provider, human,
# project administration, filesystem, network or arbitrary tool approvals.
SCRUM_WORK_TOOLS=('work_start','next_work','task_get','task_search','task_plan','task_claim','task_release','task_update','task_changes','work_finish','agent_checkpoint','file_locks','note_check')

def skill_options():
    """Hide optional Scrum catalog entries for this run; files stay readable."""
    import tomllib
    home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
    try:
        config=tomllib.loads((home/'config.toml').read_text(encoding='utf-8')) if (home/'config.toml').exists() else {}
        existing=config.get('skills',{}).get('config',[])
        if not isinstance(existing,list):return []
        entries=[dict(item) for item in existing]
        known={os.path.normcase(str(Path(item['path']).resolve())) for item in entries}
        core={'scrum-work','scrum-resume','scrum-close','scrum-complete','scrum-usage','scrum-rules'}
        for base in [home/'skills',Path.home()/'.agents/skills']:
            for path in sorted(base.glob('scrum-*')):
                if path.name not in core and (path/'SKILL.md').is_file() and os.path.normcase(str(path.resolve())) not in known:
                    entries.append({'path':str(path),'enabled':False})
        value='['+','.join('{path='+json.dumps(item['path'])+',enabled='+str(bool(item.get('enabled',True))).lower()+'}' for item in entries)+']'
        return ['-c','skills.config='+value] if entries else []
    except (OSError,ValueError,KeyError,TypeError):return []

def command(client,folder,prompt,model='',policy_folder=None):
    model=model_name(model)
    model_args=['--model',model] if model else []
    executable=codex_executable() if client=='codex' else shutil.which(client)
    if not executable:
        raise RuntimeError(client+' CLI not found in PATH')
    if client=='codex':
        options=['-c','approval_policy="on-request"','-c','approvals_reviewer="auto_review"']
        options+=skill_options()
        import mcp_selection
        options+=mcp_selection.codex_options(policy_folder or folder)
        options+=['-c','default_permissions="scrum-worker"','-c','permissions.scrum-worker.extends=":workspace"']
        rules=worker_filesystem(policy_folder or folder)
        options+=['-c','permissions.scrum-worker.filesystem={'+','.join(json.dumps(path)+'='+json.dumps(mode) for path,mode in rules.items())+'}']
        options+=['-c','mcp_servers.scrum-studio.tool_timeout_sec=180','-c','mcp_servers.scrum-studio.enabled=true','-c','mcp_servers.scrum-studio.command='+json.dumps(sys.executable),'-c','mcp_servers.scrum-studio.args='+json.dumps([str(ROOT/'mcp_server.py'),'--worker-schema']),'-c','mcp_servers.scrum-studio.enabled_tools='+json.dumps(SCRUM_WORK_TOOLS),'-c','mcp_servers.scrum-studio.default_tools_approval_mode="approve"']
        return [executable,*options,'exec','--ephemeral','--json','--skip-git-repo-check','--cd',str(folder),*model_args,'-']
    # Claude CLI: same worker catalog as Codex. A config file (not inline JSON)
    # survives Windows .cmd quoting. Preserve configured project MCP servers
    # (for example Unity or codebase-memory) instead of hiding required tools. Headless -p cannot
    # answer permission prompts, so the Scrum tools and workspace edits are
    # pre-approved explicitly; nothing bypasses permissions.
    config,strict=claude_mcp_config(policy_folder or folder,with_mode=True)
    return [executable,'-p','Follow the Scrum task instructions supplied on stdin.','--output-format','stream-json','--verbose',*model_args,
            '--mcp-config='+str(config),*(['--strict-mcp-config'] if strict else []),'--permission-mode=acceptEdits',
            '--allowedTools='+','.join(CLAUDE_WORK_TOOLS+tuple('mcp__scrum-studio__'+name for name in SCRUM_WORK_TOOLS))]

CLAUDE_WORK_TOOLS=('Read','Edit','Write','Glob','Grep','Bash(python:*)','Bash(python3:*)','Bash(py:*)','Bash(git status:*)','Bash(git diff:*)')

def claude_mcp_config(folder=None,with_mode=False):
    import mcp_selection
    folder=Path(folder) if folder is not None else ROOT
    servers,strict=mcp_selection.claude_config(folder,{'type':'stdio','command':sys.executable,'args':[str(ROOT/'mcp_server.py'),'--worker-schema']})
    path=folder/'.scrum/local/claude-worker-mcp.json'
    text=json.dumps({'mcpServers':servers},indent=2)
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        if path.read_text(encoding='utf-8')==text:return (path,strict) if with_mode else path
    except FileNotFoundError:pass
    fd,tmp=tempfile.mkstemp(prefix='claude-worker-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(text)
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)
    return (path,strict) if with_mode else path


def preclaim(rpc,project,actor,client,task,observed):
    """Claim the offered task before inference; never launch an unclaimed worker."""
    try:
        result=rpc.call('work_start',{'project':project,'actor':actor,'client':client,'id':task,'claim':True,**observed})
    except RuntimeError as error:
        message=str(error)
        return {'should_stop':True,'reason':'Task reservation failed: '+message[:500],**({'git_recovery':message[:1000]} if 'Git branch preparation failed:' in message else {})}
    if result.get('checkpoint',{}).get('should_stop'):
        return {'should_stop':True,'reason':result['checkpoint'].get('reason','Stop/quota policy')}
    claim=result.get('claim') or {}
    if not claim.get('claimed') or not result.get('task'):
        busy=claim.get('branch_busy') or {}
        branch_reason=('Checkout branch '+str(busy.get('current_branch','unknown'))+' cannot switch to '+str(busy.get('wanted','task branch'))+': '+str(busy.get('reason') or 'uncommitted work must be preserved')) if busy else ''
        reason=result.get('claim_deferred') or claim.get('reason') or branch_reason or 'Task ownership was not granted; inspect dependencies, file reservations and workflow policy'
        return {'should_stop':True,'reason':str(reason)[:500],**({'git_recovery':str(reason)[:1000]} if 'Git branch preparation failed:' in str(reason) else {})}
    return {'task':result['task'],'token':claim['token'],**{k:claim[k] for k in ('dirty_planned','git') if k in claim}}

def preclaimed_prompt(started):
    task=started['task']
    return ('\nThe supervisor already ran work_start and claimed '+str(task.get('code'))+' for you (In Progress). Do NOT call work_start or task_claim again, or repeat task_get for the same scope. Pending task reminders may still be fetched on demand.'
            ' Its token is '+json.dumps(started['token'])+'; pass it to task_update/work_finish and never print or log it.'
            ' Claim diagnostics: '+json.dumps({k:started[k] for k in ('dirty_planned','git') if k in started},separators=(',',':'))+
            ' Full task (acceptance criteria, checklist, files, evidence, continuity): '+json.dumps(task,ensure_ascii=False,separators=(',',':'))+
            '\nIf continuity is present, resume from it and never redo recorded work. Edit, verify, then finish in one call: work_finish with evidence and continuity if anything remains.'
            ' Quota is supervised outside the model; call agent_checkpoint only to pause or when a long step ends.')

def chat_event(rpc,project,actor,text):
    # Visibility must not interrupt a worker or invalidate a committed handoff.
    try:rpc.call('project_chat',{'project':project,'actor':actor,'text':text[:2000]})
    except RuntimeError:print('Project chat unavailable; consult the local worker log.',flush=True)


def release_launch(rpc,project,actor):
    try:return rpc.call('worker_launch',{'project':project,'actor':actor,'action':'release'})
    except RuntimeError:print('Startup offer cleanup unavailable; the offer expires automatically.',flush=True)


def run_failure(path,offset):
    """Extract bounded provider error events, never raw tool/model-catalog output."""
    try:
        with open(path,'rb') as stream:
            stream.seek(0,2);end=stream.tell();stream.seek(max(offset,end-131072))
            lines=stream.read(131072).decode('utf-8',errors='replace').splitlines()
    except OSError:return ''
    message=''
    for line in lines:
        try:event=json.loads(line)
        except ValueError:
            if line.startswith('Error loading config.toml:'):
                message='Codex could not load config.toml: '+line.partition(':')[2].strip()[:300]
            elif message.startswith('Codex could not load config.toml:') and line.startswith('in `mcp_servers.'):
                message+=' '+line[:180]
            continue
        if not isinstance(event,dict):continue
        if event.get('type')=='error':value=event.get('message')
        elif event.get('type')=='turn.failed':value=event.get('error',{}).get('message') if isinstance(event.get('error'),dict) else None
        elif event.get('type')=='result' and event.get('is_error') is True:
            errors=event.get('errors',[])
            value=event.get('result') or ('; '.join(v for v in errors if isinstance(v,str)) if isinstance(errors,list) else '')
        else:continue
        if not isinstance(value,str):continue
        try:
            nested=json.loads(value)
            if isinstance(nested,dict):value=nested.get('error',{}).get('message',value) if isinstance(nested.get('error'),dict) else nested.get('message',value)
        except ValueError:pass
        if isinstance(value,str):message=value[:800]
    return message


def reported_model(path,offset):
    """Only structured initialization metadata; never tool output or catalogs."""
    try:
        with open(path,'rb') as stream:
            stream.seek(offset);lines=stream.read(131072).decode('utf-8',errors='replace').splitlines()
    except OSError:return ''
    for line in lines:
        try:event=json.loads(line)
        except ValueError:continue
        if isinstance(event,dict) and event.get('type')=='system' and event.get('subtype')=='init' and isinstance(event.get('model'),str):
            try:return model_name(event['model'])
            except ValueError:return ''
    return ''


def model_mismatch(requested,actual):
    # Explicit Claude API IDs can be checked exactly; aliases resolve dynamically.
    expected=re.sub(r'\[[^\]]+\]$','',requested)
    return bool(actual and expected.startswith('claude-') and expected!=actual)


def final_message(path,offset):
    """Read only the bounded tail of this run; never publish tool/reasoning logs."""
    try:
        with open(path,'rb') as stream:
            stream.seek(0,2);end=stream.tell();begin=max(offset,end-131072)
            stream.seek(begin)
            if begin>offset:stream.readline()
            lines=stream.read().decode('utf-8',errors='replace').splitlines()
    except OSError:return ''
    final=''
    for line in lines:
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict):continue
        item=event.get('item')
        if event.get('type')=='item.completed' and isinstance(item,dict) and item.get('type')=='agent_message':
            value=item.get('text')
        elif event.get('type')=='result':value=event.get('result')
        else:continue
        if isinstance(value,str):final=value
    return final[:1500]


def stop_child(child, grace=10):
    if child.poll() is not None:
        return
    try:
        if os.name=='nt':
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(child.pid,signal.SIGINT)
        child.wait(timeout=grace)
        return
    except (OSError,subprocess.TimeoutExpired):
        pass
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    else:
        os.killpg(child.pid,signal.SIGKILL)
    child.wait(timeout=10)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=int,required=True)
    parser.add_argument('--client',choices=['codex','claude'],required=True)
    parser.add_argument('--worker',default='primary')
    parser.add_argument('--allow-run',action='store_true',help='Explicitly allow Wakeup to launch the selected CLI')
    parser.add_argument('--usage-file',help='Optional absolute telemetry JSON file; required for Claude unless usage_guard is disabled by a human')
    parser.add_argument('--profile',help='Managed profile ID; role is loaded from the project')
    parser.add_argument('--start-now',action='store_true',help='Explicit immediate-start request; quotas and Stop still apply')
    parser.add_argument('--usage-once',action='store_true',help='Read usage without starting a model or worker')
    args=parser.parse_args()
    if args.usage_once:
        print(json.dumps(usage_file(args.usage_file) if args.usage_file else codex_usage() if args.client=='codex' else {}));return
    if not args.allow_run:
        raise SystemExit('Start explicitly with --allow-run to authorize CLI execution from Wakeup. No worker was started.')
    if not re.fullmatch('[A-Za-z0-9_-]{1,40}',args.worker):
        raise SystemExit('Worker name must be 1..40 letters/digits/_/-')
    rpc=ScrumClient()
    child=None;output=None;actor=None;log_path=None;log_offset=0;selected_model='';seen_model='';offered_task=None;launch_selection={};reroutes=0;batch_run=False;run_id='';run_tasks=[]
    usage_pool=ThreadPoolExecutor(max_workers=1)
    usage_future=None
    try:
        project=next((p for p in rpc.call('projects',{}) if p['id']==args.project),None)
        if not project or not project.get('uuid') or not project.get('available'):
            raise RuntimeError('Open an available folder-scoped project first')
        folder=Path(project['path'])
        managed=rpc.call('agent_manage',{'project':args.project,'action':'get','id':args.profile,'include_instructions':True}) if args.profile else None
        if managed and managed['client']!=args.client:raise RuntimeError('Profile client mismatch')
        role=managed['instructions'] if managed else ''
        actor=managed['actor'] if managed else rpc.call('user_register',{'identity':f"agent:bridge:{project['checkout']}:{args.client}:{args.worker}",'name':f'{args.client} · {args.worker}','kind':'agent'})['id']
        status=rpc.call('agent_status',{'project':args.project})
        if any(a['actor']==actor and a.get('bridge_connected') for a in status['agents']):
            raise RuntimeError('This worker already has a connected bridge; choose a distinct --worker')
        generation=status['control']['generation']
        attempted=generation # Ordinary bridge startup never consumes old Wakeup.
        pending_start=args.start_now
        pending_auto=False
        recovery_attempts=set()
        checkout_attempts=set()
        incomplete_attempts={};pending_recovery=None
        profile_generation=managed['generation'] if managed else 0
        last_usage=float('-inf');last_policy=0;policy={};observed={}
        last_checkpoint=None;previous_running=False;control_stamp=idle_signals.stamp(folder)
        print('Bridge connected; press Wakeup in the project. Stop with Ctrl+C.',flush=True)
        while True:
            at=time.monotonic()
            signal_stamp=idle_signals.stamp(folder)
            signal_changed=signal_stamp!=control_stamp
            control_stamp=signal_stamp
            running=child is not None and child.poll() is None
            usage_ready=usage_future is not None and usage_future.done()
            pending_ready=(pending_start or pending_auto) and usage_future is None
            if not idle_signals.checkpoint_due(at,last_checkpoint,running,previous_running,signal_changed) and not (child is not None and not running) and not pending_ready and not usage_ready:
                time.sleep(2)
                continue
            last_checkpoint=at;previous_running=running
            if not policy or signal_changed or at-last_policy>=300:
                policy=rpc.call('project_settings',{'project':args.project});last_policy=at
            if usage_future is not None and usage_future.done():
                try:
                    observed=usage_future.result()
                except Exception:
                    observed={}
                usage_future=None
            wants_usage=running or pending_start or pending_auto or signal_changed or policy.get('auto_resume_session_quota',False) or not observed
            if wants_usage and at-last_usage>=policy.get('usage_check_seconds',60) and usage_future is None:
                # A slow provider request must never block the local Stop loop.
                usage_future=usage_pool.submit(usage_file,args.usage_file) if args.usage_file else usage_pool.submit(codex_usage) if args.client=='codex' else None
                if args.client=='claude' and not args.usage_file:
                    observed={}
                last_usage=at
            report=rpc.call('agent_checkpoint',dict(project=args.project,actor=actor,client=args.client,action='observe',running=running,**({'profile_id':args.profile} if args.profile else {}),**observed))
            actual=reported_model(log_path,log_offset) if log_path and selected_model else ''
            if actual and actual!=seen_model:
                seen_model=actual
                chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: requested model {selected_model}; CLI reported {actual}.')
            if child and model_mismatch(selected_model,actual):
                release_launch(rpc,args.project,actor)
                stop_child(child);output.close();output=None;child=None;running=False
                error=f'Requested model {selected_model}, but CLI reported {actual}. Run stopped; select an available exact model or an intentional provider alias. No replacement was selected by Scrum Studio.'
                chat_event(rpc,args.project,actor,error)
                rpc.call('agent_checkpoint',{'project':args.project,'actor':actor,'client':args.client,'action':'offline','reason':'Provider model mismatch','run_exit_code':-1,'run_error':error,'cli_usage_report':execution_usage.report(log_path,log_offset,args.client,run_id,selected_model,run_tasks),'summary':'Worker stopped after model mismatch; validate any partial edits before resuming.'})
            if child and not running:
                released=release_launch(rpc,args.project,actor) or {}
                output.close();output=None
                answer=final_message(log_path,log_offset)
                failed=run_failure(log_path,log_offset) if child.returncode else ''
                error=(failed or 'CLI failed before a final response; inspect the local worker log.') if child.returncode else ''
                chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: CLI finished (exit {child.returncode}).'+('\nError: '+error if error else '\n'+answer if answer else ' No final response was captured; inspect the local worker log.'))
                finished={}
                if not error and offered_task is not None:
                    finished=rpc.call('task_get',{'project':args.project,'id':released.get('batch_current',offered_task)})
                retry=recover_incomplete(finished,actor,incomplete_attempts,report,child.returncode)
                unfinished=unfinished_owned(finished,actor)
                summary=f'CLI exit {child.returncode}. '+(error or 'Consult task history before resuming.')
                if unfinished:
                    summary=('Worker exited before closing this task. '+('One automatic closure recovery is queued. ' if retry else 'Automatic recovery limit/policy reached; do not loop. ')+
                             'Resume existing edits and remaining acceptance/Git closure. Retain verified checks when their inputs are unchanged; inspect the existing report before any rerun. Last worker response (verify claims against artifacts): '+answer)[:2000]
                rpc.call('agent_checkpoint',{'project':args.project,'actor':actor,'client':args.client,'action':'offline','reason':report['reason'] if report['should_stop'] and report['reason'].startswith('5h usage ') else 'CLI failed' if child.returncode else 'Task closure incomplete' if unfinished else 'CLI session ended','run_exit_code':child.returncode,'run_error':error,'cli_usage_report':execution_usage.report(log_path,log_offset,args.client,run_id,selected_model,released.get('batch_tasks',run_tasks)),'summary':summary})
                if not error and offered_task is not None and not report['should_stop']:
                    from model_routing import request
                    previous_selection=released.get('batch_selection') or launch_selection
                    current_request=request(finished);previous=previous_selection.get('request',current_request)
                    changed=current_request['model']!=previous['model'] or (previous_selection.get('source')=='automatic' and current_request['model_tier']!=previous['model_tier'])
                    if retry:
                        pending_recovery=finished['id'];incomplete_attempts[pending_recovery]=incomplete_attempts.get(pending_recovery,0)+1
                        pending_start=True
                        chat_event(rpc,args.project,actor,'Incomplete task closure detected. One recovery run will resume the same task from saved results; no tests or completion are assumed.')
                    elif continue_after_task(finished,batch_run,released):pending_start=True;reroutes=0;incomplete_attempts.pop(finished.get('id'),None)
                    elif changed and reroutes<2:pending_start=True;reroutes+=1
                child=None
            if running and report['should_stop']:
                stop_child(child)
                released=release_launch(rpc,args.project,actor) or {}
                output.close();output=None;child=None
                answer=final_message(log_path,log_offset)
                chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: interrupted after Stop/quota signal. Task handoff and own lock release follow.'+('\nLast captured response (may predate interruption): '+answer if answer else ' No final response was captured; task handoff uses recorded progress.'))
                rpc.call('agent_checkpoint',{'project':args.project,'actor':actor,'client':args.client,'action':'pause','reason':report['reason'],'summary':'','cli_usage_report':execution_usage.report(log_path,log_offset,args.client,run_id,selected_model,released.get('batch_tasks',run_tasks))})
            if managed and not report.get('profile_enabled',True):break
            pending_auto=pending_auto or report.get('auto_resume',{}).get('requested',False)
            if report['control']['paused'] or report['should_stop'] or report.get('auto_resume',{}).get('status','').startswith(('disabled','cancelled')):
                pending_auto=False
            current=report['control']['generation']
            profile_request=report.get('profile_generation',profile_generation)>profile_generation
            recovery_id=report.get('git_recovery_id')
            recovery_pending=recovery_id is not None and recovery_id not in recovery_attempts and not report['should_stop']
            immediate=(pending_start or profile_request or pending_auto or recovery_pending) and usage_future is None
            if not child and usage_future is None and (current>attempted or immediate) and not report['control']['paused']:
                attempted=current
                pending_start=False
                automatic=pending_auto
                pending_auto=False
                profile_generation=report.get('profile_generation',profile_generation)
                if report['should_stop']:
                    print('Wakeup held: '+report['reason'],flush=True)
                    chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: Wakeup held: '+report['reason'])
                else:
                    try:
                        wanted=pending_recovery or recovery_id
                        pending_recovery=None
                        offer=rpc.call('worker_launch',dict(project=args.project,actor=actor,action='reserve',client=args.client,**({'id':wanted} if wanted else {})))
                        if recovery_id:recovery_attempts.add(recovery_id)
                    except RuntimeError as error:
                        print('Wakeup preflight unavailable; no model started.',flush=True)
                        chat_event(rpc,args.project,actor,'Wakeup preflight failed; no model started: '+str(error)[:500])
                        time.sleep(2)
                        continue
                    if not offer.get('eligible'):
                        if policy.get('agent_followup_tasks') and offer.get('checkout_recovery'):
                            try:
                                queued=queue_checkout_recovery(rpc,args.project,actor,offer,checkout_attempts)
                                if queued:
                                    pending_recovery=queued;pending_start=True
                                    chat_event(rpc,args.project,actor,'Checkout blocks ready work. A scoped recovery task was registered/reused; normal Stop, quota and claim checks apply.')
                            except RuntimeError as error:chat_event(rpc,args.project,actor,'Could not register checkout recovery: '+str(error)[:400])
                        reason=offer.get('reason','No eligible unreserved work; no model was started.')
                        print('Wakeup skipped: '+reason,flush=True)
                        chat_event(rpc,args.project,actor,'Wakeup skipped: '+reason)
                        time.sleep(2)
                        continue
                    prompt=f'Use scrum-work and scrum-resume for project {args.project} in {folder}. Your persistent agent actor is {actor}; reuse it. This human Wakeup authorizes the existing backlog. Claim an eligible task/files (claimed=true) ONLY for '+str(offer.get('code'))+' (id '+str(offer.get('id'))+'). Complete all acceptance, verify, work_finish(close_session=true), exit; the supervisor will continue with the next eligible task. Partial implementation alone is no reason to hand off. Respect Stop/quota/WIP/dependencies/reservations; save unfinished facts only for a real interruption, retain valid checks, never poll models.'
                    if policy.get('agent_followup_tasks'):
                        prompt+=' The human enabled scoped autonomous follow-up tasks. For a concrete blocker within the authorized outcome, search existing work and use task_plan to create/refine minimal recovery/prerequisite tasks with acceptance criteria and real dependencies. If recovery must reuse partial edits on this branch, keep the same context_group. Do this only when needed, not at every task. Save/release current work before switching; the supervisor selects and claims eligible work. Never invent speculative backlog.'
                    prompt+=' Failed verification is work to diagnose, not by itself an external blocker. Fix scoped code, missing references and render/capture setup in this task; use a changed hypothesis and targeted check, never an unchanged retry loop. Blocked requires a concrete unmet prerequisite or external constraint, with attempted repair and the action needed to resume; a retry count alone is not a blocker. If a session must end while repair remains possible, save continuity and release as TODO instead. Required verification still pending means not Done. Reserve supporting files and Unity .meta sidecars belonging to your outputs; acquired reservations also extend the Git file plan.'
                    if offer.get('batch'):
                        prompt+='\nSequential batch headers (max3; load each scope only as needed): '+json.dumps(offer['batch'],separators=(',',':'))+'. Reuse this context. Work only on the current task; others stay unclaimed/TODO. Complete each using work_finish(use_batch=true,client=\"'+args.client+'\") without close_session. Continue ONLY with the returned next task after a fresh claim; when batch_done/session offline, exit. The server rechecks model/dependencies/locks. Never mark the whole batch In Progress.'
                        prompt=prompt.replace('Verify, work_finish(close_session=true), exit;','Verify each task individually, then exit when the batch is finished;').replace('ONLY for '+str(offer.get('code')),'starting with '+str(offer.get('code')))
                    prompt+='\nFollow the installed scrum-work skill; read its references/core-guidance.md only for batches or MCP recovery; resolve installed skill references relative to each skill directory, never the target checkout. Preflight, next_work, task_search and batch headers are navigation only: load current full scope with task_get or work_start(id) before editing, and preserve its acceptance criteria, blockers, checklist, evidence and continuity. If the task has continuity, resume from it and never redo recorded work or re-create tasks. Claim only the current task/files. Use codebase-memory when installed, otherwise direct source reads. Specialist skills/tools are loaded only on demand.'
                    prompt+=' Reuse human authorization already granted for installed project tools. Do not claim approval is pending without an actual request; report real permission blockers through task continuity and Chat. Never bypass a reviewer rejection. On a recoverable tool error, inspect the concrete error and attempt a bounded authorized repair before exiting; never repeat an unchanged failing launch. If installed Unity cannot access its per-user configuration or licensing helper inside the sandbox, request normal reviewed execution of the specific test command; do not change licensing, broaden AppData access, or treat startup failure as test evidence. Required tests need a fresh machine-readable report with executed tests and a passing result, not just exit code 0; leave work unfinished when acceptance is unverified.'
                    if any(path.endswith('/Unity/Caches') and mode=='write' for path,mode in worker_filesystem(folder).items()):
                        prompt+='\nUnity tests: use '+json.dumps(str(ROOT/'scripts/unity_verify.py'))+' --editor INSTALLED_EDITOR --project UNITY_ROOT [--filter TEST_NAME]. Windows Unity licensing IPC/cache may be inaccessible inside the sandbox: when access is known to be blocked, request normal reviewed execution of this exact helper command upfront, using the supplied worker Python. If a sandbox attempt reports environment_access, actually submit the reviewed retry in this same turn; writing a future instruction is not recovery. Inspect the newest complete log: old licensing warnings may precede real compiler errors. Fix scoped compiler/test errors, then verify the changed inputs. A null font/reference or blank capture requires diagnosing asset binding, canvas/camera and render timing; it does not establish an external Unity failure. For a proven external constraint after actual reviewed recovery, record the concrete Blocked reason and required external action, close safely, and exit; the supervisor can take other eligible work. Never change license/account or bypass a reviewer rejection.'
                    prompt+='\nHuman decision needed: use request_user_input if available, otherwise the supplied Python + '+json.dumps(str(ROOT/'scripts/ask_human.py'))+' --project '+str(args.project)+' --actor '+str(actor)+' --question TEXT --options JSON_ARRAY. The local helper shows Chat buttons and waits without model polling. Never send secrets or invent approval; wait for the actual answer.'
                    if recovery_id:
                        prompt=prompt.replace('This human Wakeup authorizes the existing backlog.','Human-enabled Git automation authorizes this scoped integration recovery only. Preserve existing work; do not repeat implementation. If blocked, record the blocker and exit without retry loops.')
                    if automatic:
                        prompt=prompt.replace('This human Wakeup authorizes the existing backlog.','Human-enabled session-quota autoresume continues the previously authorized backlog after a verified reset.')
                    if role:
                        prompt+='\nRole instructions:\n'+role+'\nScrum ownership, quota, Stop and verification rules apply; do not weaken them.'
                    prompt+='\nPython: '+json.dumps(worker_python())+' (PowerShell: &). Quota is refreshed by this supervisor; do not run --usage-once or probe usage at startup. Planning/attachments/specialist tools or stalled MCP: this executable + '+json.dumps(str(ROOT/'scripts/mcp_client.py'))+' --tool TOOL, JSON stdin; default timeouts cover Git completion; never print tokens. If a helper is denied, request the minimum real tool escalation through auto_review rather than abandoning authorized work.'
                    prompt+='\nValidated provider quota: '+json.dumps(observed)+'. Start AND claim in one call: work_start with actor/project/client, id=' + str(offer.get('id')) + ', claim=true (files only to change the planned set) and these fields while original observed_at is fresh; never replace the timestamp. claim.claimed=true gives the token; use task_claim only after claim_deferred or claimed=false. Supervisor refreshes quota; safe agent_checkpoint reuses it, no startup probe. Approval-required calls strictly sequentially; wait for results. On sandbox denial request the minimum required access normally (skills/Python included). Never bypass reviewer rejection or weaken sandbox/quota policy.'
                    # Supervisor pre-claim: start the session and claim the offered task
                    # in local code, so the model spends no turns on work_start/task_get.
                    started=preclaim(rpc,args.project,actor,args.client,offer['id'],observed)
                    if started.get('should_stop'):
                        release_launch(rpc,args.project,actor)
                        rpc.call('agent_checkpoint',{'project':args.project,'actor':actor,'client':args.client,'action':'offline','reason':'Launch held: '+started['reason'],'summary':'No model launched; task reservation or policy preflight did not permit execution.'})
                        if policy.get('agent_followup_tasks') and started.get('git_recovery'):
                            import git_sync
                            state=git_sync.state(folder)
                            if state:
                                issue={'branch':state[0],'head':git_sync._git(folder,'rev-parse','HEAD').stdout.strip(),'files':state[1][:100],'error':started['git_recovery']}
                                try:
                                    queued=queue_checkout_recovery(rpc,args.project,actor,{'id':offer['id'],'checkout_recovery':issue},checkout_attempts)
                                    if queued:pending_recovery=queued;pending_start=True
                                except RuntimeError as error:chat_event(rpc,args.project,actor,'Git preparation recovery could not be registered: '+str(error)[:400])
                        print('Wakeup held: '+started['reason'],flush=True)
                        chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: Wakeup held: '+started['reason'])
                        time.sleep(2)
                        continue
                    if started.get('task'):
                        prompt=prompt[:prompt.index('\nValidated provider quota: ')].replace('Claim an eligible task/files (claimed=true) ONLY for ','Work ONLY on ')+preclaimed_prompt(started)
                    if args.client=='codex' and policy.get('agent_approval_mode')=='Full access':
                        prompt+='\nThis launch uses human-authorized Full access with approval policy never. Execute authorized project commands directly; previous sandbox-only failures do not establish a current access failure. The escalation advice above applies only to sandboxed launches. Do not request permission that this launch already has. Actual organization/OS restrictions still apply; diagnose real failures and attempt a bounded authorized repair, without weakening Stop, quota, ownership or verification.'
                    run_folder=Path(started.get('git',{}).get('worktree') or folder)
                    if run_folder!=folder:
                        prompt=prompt.replace(' in '+str(folder)+'.',' in '+str(run_folder)+'.')
                        prompt+='\nWorking directory: '+str(run_folder)+'. The board stays at '+str(folder)+'; keep its project ID. Edit only this worktree. Use relative file plans so central reservations match across workers. Do not open/register this worktree as a new Scrum project. Verify graph results against source in this worktree.'
                    log_path=folder/'.scrum'/'local'/('worker-'+args.client+'-'+args.worker+'.log')
                    output=open(log_path,'a',encoding='utf-8');log_offset=output.tell()
                    options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
                    try:
                        offered_task=offer['id'];launch_selection=offer.get('selection',{});batch_run=bool(offer.get('batch'));run_id=uuid.uuid4().hex;run_tasks=[item['id'] for item in offer.get('batch',[])] or [offered_task]
                        selected_model=launch_selection.get('model',report.get('profile_model',managed.get('model','') if managed else ''))
                        seen_model=''
                        with tempfile.TemporaryFile(mode='w+b') as prompt_input:
                            prompt_input.write(prompt.encode('utf-8'));prompt_input.seek(0)
                            launch=interactive_command(command(args.client,run_folder,prompt,selected_model,policy_folder=folder),args.client,args.project,actor,run_folder,selected_model,policy)
                            child=subprocess.Popen(launch,cwd=run_folder,stdin=prompt_input,stdout=output,stderr=subprocess.STDOUT,env={**os.environ,'SCRUM_BRIDGE_WORKER':'1'},**options)
                    except (OSError,RuntimeError,ValueError) as error:
                        run_id=''
                        release_launch(rpc,args.project,actor)
                        output.close();output=None
                        chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: launch failed: {error}')
                        rpc.call('agent_checkpoint',{'project':args.project,'actor':actor,'client':args.client,'action':'offline','run_exit_code':-1,'run_error':str(error)[:800],'reason':'CLI launch failed'})
                    else:
                        chat_event(rpc,args.project,actor,f'{"Session quota reset" if automatic else "Wakeup"} -> {args.client} worker {args.worker}; offered {offer.get("code")}. Results and next step belong to the task.')
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        if child and child.poll() is None:
            stop_child(child)
        if output:output.close()
        if actor:
            release_launch(rpc,args.project,actor)
            try:
                rpc.call('agent_checkpoint',{'project':args.project,'actor':actor,'client':args.client,'action':'offline','summary':'Bridge disconnected. Resume from task history; validate partial edits.',**({'cli_usage_report':execution_usage.report(log_path,log_offset,args.client,run_id,selected_model,run_tasks)} if run_id and log_path else {})})
            except RuntimeError:
                pass
        rpc.close()
        usage_pool.shutdown(wait=False,cancel_futures=True)

if __name__=='__main__':
    main()
