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
from mcp_client import ScrumClient,ToolError
from provider_usage import codex_usage, usage_file
from cli_runtime import codex_executable,model_name,worker_python

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import execution_usage
import idle_signals
import run_cost
import usage_measurement
from worker_catalog import EXECUTION_TOOLS


def usage_checkpoint(rpc,payload):
    try:return rpc.call('agent_checkpoint',payload)
    except ToolError as error:
        # A returned schema rejection is known not to have committed. Never
        # retry an uncertain transport write or lose Stop/handoff for metrics.
        if 'cli_usage_report' not in payload or 'arguments.cli_usage_report' not in str(error):raise
        print('CLI usage schema rejected; worker handoff continues without that report. Raw counters remain in the local worker log.',flush=True)
        return rpc.call('agent_checkpoint',{k:v for k,v in payload.items() if k!='cli_usage_report'})


def measured_report(folder,*args):
    report=execution_usage.report(*args)
    try:usage_measurement.record_run(folder,report)
    except (OSError,ValueError,KeyError):pass
    return report


def finish_measurement(folder,reason,rpc,project,client='codex'):
    try:return usage_measurement.finish(folder,reason,rpc,project,client=client)
    except (OSError,ValueError,KeyError):return None


def continue_after_task(task, batch_run, released):
    # A real impediment releases this task; another eligible task may proceed.
    # Ordinary chaining never retries In Progress; closure recovery is bounded
    # separately and targets only the same unfinished task.
    if task.get('status') in ('Blocked','Cancelled'):return True
    if task.get('status')=='In Review' and task.get('review',{}).get('required') and task['review'].get('state')=='pending' and task.get('evidence'):return True
    # A verified Done result is progress even if an old worker closed its batch
    # early. Release has already removed its reservations; native selection can
    # safely choose remaining work. Stop/quota gates are checked by the caller.
    return task.get('status')=='Done'


def usage_retry_intent(report,requested,held,policy,usage_ready):
    """Remember an authorized start through unknown telemetry, without polling AI."""
    if report['control']['paused'] or not report.get('profile_enabled',True):return False,False
    reason=str(report.get('reason',''))
    if report.get('should_stop') and reason.startswith('Usage unknown'):
        return held or requested,False
    if report.get('should_stop') and reason.startswith(('5h usage ','weekly usage ')) and not policy.get('auto_resume_session_quota'):
        return False,False
    if held and usage_ready and not report.get('should_stop'):return False,True
    return held,False


def waiting_for_fresh_usage(report,usage_pending):
    return (usage_pending and not report['control']['paused'] and report.get('profile_enabled',True)
            and str(report.get('reason','')).startswith('Usage unknown or stale'))


def preflight_transport_delay(error,attempt):
    """Only reconcile/retry launch offers, never an uncertain task claim."""
    message=str(error).lower()
    if any(word in message for word in ('permission','access denied','quota','usage limit','unauthorized','git branch preparation')):return None
    if not any(word in message for word in ('timed out','connection closed','connection reset','broken pipe','server exited','transport')):return None
    return (2,5)[attempt] if 0<=attempt<2 else None


def unfinished_owned(task,actor):
    return task.get('owner_id')==actor and (task.get('status')=='In Progress' or
        (task.get('status')=='TODO' and bool(task.get('continuity')) and not task.get('blocked_reason')))


def recover_incomplete(task,actor,attempts,report,exit_code):
    """One bounded closure recovery; never infer completion from exit code 0."""
    return (exit_code==0 and unfinished_owned(task,actor)
            and attempts.get(task.get('id'),0)<1 and not report.get('should_stop')
            and not report.get('control',{}).get('paused') and report.get('profile_enabled',True))


def capacity_retry(error, attempts, report):
    """Only explicit provider overload is retryable; no model fallback."""
    if (report.get('should_stop') or report.get('control',{}).get('paused')
            or not report.get('profile_enabled',True)):
        return None
    message=str(error).lower()
    if not ('serveroverloaded' in message or 'selected model is at capacity' in message):
        return None
    return (30,90,180)[attempts] if attempts<3 else None


def queue_checkout_recovery(rpc,project,actor,offer,attempted):
    issue=offer.get('checkout_recovery')
    if not issue:return False
    fingerprint=json.dumps(issue,sort_keys=True)
    if fingerprint in attempted:return False
    attempted.add(fingerprint)  # An uncertain write must not cause a retry loop.
    branch=str(issue['branch'])
    result=rpc.call('task_plan',{'project':project,'actor':actor,'items':[{
        'key':'checkout-recovery','title':('Recover checkout for available work: '+branch)[:185]+' '+hashlib.sha256(fingerprint.encode()).hexdigest()[:8],
        'description':'The human enabled autonomous recovery tasks. Ready authorized work cannot start because of checkout/preparation state on '+branch+'. Inspect saved task continuity and current Git state, reserve discovered files, and recover branch availability without losing work. Preserve unrelated edits/staging; no blanket commit, discard, stash or force push. Commit/publish only verified related outcomes under the recorded Git policy. If a completed prerequisite is not integrated, reconcile its recorded parent chain and publish the verified commits needed by the waiting task; unfinished context-group work must stay unfinished. If recovery needs an external action, record a precise blocker and exit; never repeat an unchanged attempt. For a remaining obstruction caused ONLY by dirty checkout files/staging, set blocked_reason_kind=checkout when blocking this Git recovery task; it will reopen for verification after the checkout is clean. Keep external for permissions, credentials, unknown provenance requiring a separate decision, or other independent constraints. Concrete error: '+str(issue.get('error','Uncommitted work prevents switching to another ready task.'))[:700]+'. Diagnostic paths: '+json.dumps(issue.get('files',[])[:20]),
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


def native_git_filesystem(folder):
    """Only this project's Git metadata, required by native claim/finish."""
    root=Path(folder).resolve();metadata=root/'.git'
    try:
        if metadata.is_file():
            pointer=metadata.read_text(encoding='utf-8').strip()
            if not pointer.startswith('gitdir:'):return {}
            metadata=(root/pointer[7:].strip()).resolve()
        if not metadata.is_dir():return {}
        rules={metadata.as_posix():'write'}
        common=metadata/'commondir'
        if common.is_file():
            shared=(metadata/common.read_text(encoding='utf-8').strip()).resolve()
            if shared.is_dir():rules[shared.as_posix()]='write'
        return rules
    except (OSError,ValueError):return {}

# Only the board workflow authorized by Start/Wakeup; never provider, human,
# project administration, filesystem, network or arbitrary tool approvals.
SCRUM_WORK_TOOLS=EXECUTION_TOOLS


def execution_prompt(project,folder,actor,client,offer,policy,role='',automatic=False,recovery=False):
    """One compact execution contract; the claimed task is appended exactly once."""
    authorization=('Human-enabled scoped Git recovery only.' if recovery else
                   'Human-enabled quota autoresume continues the authorized backlog.' if automatic else
                   'This human Wakeup authorizes the existing backlog.')
    prompt=(f'Use scrum-work for project {project} in {folder}. Your persistent agent actor is {actor}; reuse it. '
            +authorization+' Work ONLY on '+str(offer.get('code'))+' and successfully claimed batch successors, using the supervisor-supplied full scope. '
            'Resume saved facts/checks; no startup board scan or replanning. Only current work is In Progress. '
            'Headers are navigation only: load current full scope with task_get or work_start(id) only if no full claimed scope was supplied. '
            'Retain acceptance criteria, blockers, checklist, evidence and continuity. '
            'Use codebase-memory for structural queries with coverage/source fallback. Specialist skills only when needed. '
            'Resolve skill references relative to each skill directory, never the target checkout. '
            'Verify changed behavior proportionally; no routine full suite, duplicate diff review, evidence essay or successful log dump. '
            'Reuse unchanged checks with original times; leave work unfinished when acceptance is unverified. '
            'Fix scoped failures using a changed hypothesis; never repeat an unchanged failing launch. '
            'Blocked needs a concrete unmet prerequisite/external constraint. Reserve all output/support files and Unity .meta sidecars. '
            'Reuse human authorization already granted. Do not claim approval is pending without an actual request. '
            'Never bypass a reviewer rejection. Request the real reviewed command retry for sandbox access errors; do not alter accounts/licenses. ')
    if policy.get('agent_followup_tasks'):
        prompt+=('Scoped follow-up planning is enabled: only for a concrete discovered prerequisite, search/reuse before task_plan, '
                 'use defer_task=current ID, its token, prerequisites=[plan keys or IDs] and continuity to atomically park your task without rewriting acceptance. Exit on deferred=true for supervisor selection. Correct confirmed planning rejections in this run; uncertain writes still require reconciliation. No speculative backlog. ')
    if offer.get('batch'):prompt=batch_prompt(prompt,offer['batch'],client,offer.get('code'))
    else:prompt+='Finish verified work with work_finish(close_session=true,client="'+client+'"). '
    prompt+='After exit the supervisor will continue with the next eligible task. '
    prompt+='\nPython: '+json.dumps(worker_python())+' (PowerShell: &). Native MCP fallback: '+json.dumps(str(ROOT/'scripts/mcp_client.py'))+' --tool TOOL, JSON stdin; default timeouts cover Git completion; never print tokens. Quota is supervised outside the model; do not run --usage-once or probe usage at startup. '
    prompt+='For human decisions use request_user_input or this Python + '+json.dumps(str(ROOT/'scripts/ask_human.py'))+' --project '+str(project)+' --actor '+str(actor)+' --question TEXT --options JSON_ARRAY; wait for the actual answer. '
    if any(path.endswith('/Unity/Caches') and mode=='write' for path,mode in worker_filesystem(folder).items()):
        prompt+='\nUnity checks: '+json.dumps(str(ROOT/'scripts/unity_verify.py'))+' --editor INSTALLED_EDITOR --project UNITY_ROOT --reuse [--filter TEST_NAME] [--graphics on]. For environment_access submit the reviewed retry in this turn. Inspect newest complete logs; require an executed/passing machine-readable report. Diagnose null references, blank captures and frame/layout timing before blaming the environment. '
    prompt+='\nLarge generated/visual sets: native helper '+json.dumps(str(ROOT/'scripts/work_checks.py'))+' --help; read scrum-work references/efficient-checks.md only if needed. Read counts/exceptions; automated coverage plus representative variants and explicit visual cases. '
    if policy.get('autonomy_mode')=='Autonomous':
        prompt=prompt.replace('; wait for the actual answer.', '; if deferred=true, exit so other work continues.').replace('; wait for an actual answer.', '; if deferred=true, exit so other work continues.')
        prompt=prompt.replace('For human decisions use request_user_input or this Python + ', 'For indispensable typed human decisions use this Python + ').replace('For real human decisions use request_user_input or ', 'For indispensable typed human decisions use ')
    prompt+=autonomy_prompt(policy)
    if role:prompt+='\nRole instructions:\n'+role+'\nStop, quota, ownership and verification rules apply; do not weaken them.'
    return prompt


def file_scope_guidance(started):
    if started.get('file_lock_mode')!='checkout':return ''
    return ('\nSingle-worker checkout exclusivity is active: keep the task claim and explicit file plan, but do not call file_locks/lock for individual outputs. '
            'For newly discovered output files and sidecars, include only your explicit additional_files in work_finish (CLI finish --paths FILES) in the existing closing call. '
            'Do not adopt unrelated or pre-existing dirty files. This overrides generic per-file reservation wording; Stop/quota and actual permissions still apply.')


def cli_prompt(project,folder,actor,client,offer,policy,started,role=''):
    from scrum_cli import redact
    import shlex
    cli=('& '+json.dumps(worker_python())+' '+json.dumps(str(ROOT/'scrum.py'))) if os.name=='nt' else shlex.join([worker_python(),str(ROOT/'scrum.py')])
    prompt=(f'Use scrum-work for project {project}, actor {actor}, in {folder}. Human Wakeup authorizes this backlog. '
        'SCRUM coordination channel is native CLI, not MCP. This overrides legacy transport wording in checkout instructions. '
        'Use the supplied CLI only for Scrum; other configured MCP servers remain available for their own capabilities. '
        'The supervisor already claimed the full task below. Do not start, claim, rediscover, scan the board or probe quota. '
        'SCRUM_CLI_CONTEXT holds private ownership; never read/print it or supply tokens. Commands inherit it automatically. '
        'Read scrum-work references/cli.md only for command details. '
        'Execute the current acceptance, preserve existing work and reuse continuity and unchanged verification. '
        'Use codebase-memory for structural queries with coverage/source fallback; stop graph retries after transport failure. '
        'Reserve additional output files/Unity .meta sidecars before editing. Verify proportionally; no routine full suite, '
        'duplicate review essay or successful log dump. Diagnose scoped errors with a changed hypothesis before blocking. '
        'Respect Stop, quota, ownership, dependencies, physical review and actual permission review. Never bypass a rejection. '
        'An explicit design decision does not by itself mean human approval: for routine missing UI states within authorized scope, record a minimal consistent choice unless the project explicitly reserves it for the human. Never claim unreferenced states match Figma. '
        'Do not manually stash/reset/switch/publish in an ordinary task; Git integration is native at finish. '
        'CLI command: '+cli+' show; '+cli+' lock --task CODE --paths relative/file; '
        +cli+' update --task CODE --continuity "Done: ...; Next: ..."; '
        +cli+' finish --task CODE --evidence "Verified ...". Finish only verified acceptance. '
        'For complex arguments use call OPERATION --input JSON_FILE; help OPERATION shows its schema. '
        'A failed/uncertain write must be reconciled, never blindly repeated; show/status are safe, pause hands control back. '
        'Quota and idle checks are native, outside the model. ')
    if policy.get('agent_followup_tasks'):
        prompt+='Necessary scoped follow-ups are allowed: search/reuse before plan --input FILE. For a missing prerequisite of the current task, use a JSON object with items, defer_task=current ID, prerequisites=[plan keys or existing IDs], continuity; the CLI supplies the token, preserves acceptance and releases the claim/batch atomically. On deferred=true exit for supervisor selection. PLAN_REJECTED means rolled back: correct the plan in this run; only uncertain writes require pause. No speculative backlog. '
    if offer.get('batch'):
        prompt+='Reserved batch headers: '+json.dumps(offer['batch'],separators=(',',':'))+'. Other tasks remain TODO and reserved. Finish automatically requests the next fresh claim. Continue ONLY with successful next.claim and next.task, without restarting or rereading unchanged scope. Exit on batch_done, failed next claim, Stop/quota or real impediment. '
    else:prompt+='Finish closes this session. Exit; the supervisor selects subsequent eligible work. '
    if started['task'].get('category')=='Git recovery':
        prompt+='Scoped Git recovery: preserve staging/unrelated work; honor recorded parents, never force push. Reuse retained verification; retest only conflict-resolution changes. If cleanup was rejected, do not repeat deletion. After inspecting and explicitly reserving every dirty file, native helper '+json.dumps(str(ROOT/'scripts/preserve_checkout.py'))+' --target EXISTING_INTEGRATED_BRANCH can retain exact bytes/hashes and unknown tracked edits in a private local branch, move exact untracked files without deletion, and switch only to a target already containing the source commits. It refuses foreign scope, staging, conflicts and unfinished integration. Do not publish retained unknown edits or infer provenance. Request normal reviewed execution; do not bypass a rejection. '
    prompt+='Full claimed task and diagnostics: '+json.dumps(redact(started),ensure_ascii=False,separators=(',',':'))
    prompt+='\nHelpers: '+json.dumps(str(ROOT/'scripts/unity_verify.py'))+' --reuse for Unity checks; '+json.dumps(str(ROOT/'scripts/work_checks.py'))+' --help for large file sets. Require actual executed machine-readable tests; compile is not visual proof. For real human decisions use request_user_input or '+json.dumps(str(ROOT/'scripts/ask_human.py'))+f' --project {project} --actor {actor} --question TEXT --options JSON_ARRAY; wait for an actual answer.'
    if policy.get('autonomy_mode')=='Autonomous':
        prompt=prompt.replace('; wait for the actual answer.', '; if deferred=true, exit so other work continues.').replace('; wait for an actual answer.', '; if deferred=true, exit so other work continues.')
        prompt=prompt.replace('For human decisions use request_user_input or this Python + ', 'For indispensable typed human decisions use this Python + ').replace('For real human decisions use request_user_input or ', 'For indispensable typed human decisions use ')
    if started.get('file_lock_mode')=='checkout':
        prompt=prompt.replace('Reserve additional output files/Unity .meta sidecars before editing. ', 'Keep additional outputs and Unity .meta sidecars in the explicit completion file plan. ').replace(cli+' lock --task CODE --paths relative/file; ', '')
    prompt+=file_scope_guidance(started)
    prompt+=autonomy_prompt(policy)
    if role:prompt+='\nRole instructions: '+role
    return prompt


def autonomy_prompt(policy):
    standing=('\nHuman project execution instructions: '+policy['execution_instructions']) if policy.get('execution_instructions') else ''
    if policy.get('autonomy_mode')!='Autonomous':return standing
    return standing+('\nAutonomous decisions: resolve routine implementation, Git recovery, missing UI states and scoped technical errors yourself; reuse valid checks and existing authorization. Create/reuse a concrete prerequisite only when needed. Do not ask whether to continue, fix a failure or choose an ordinary implementation detail. Screenshots are internal verification, not routine human approval. Do not assume existing UI is wrong or redo verified work. Only for indispensable missing input, conflicting requirements, access, specific visual judgment or a human playtest, use ask --input FILE (or the supplied ask_human helper) with question_category, human_requirement explaining why only the human can resolve it, two or more concrete options and concise continuity. The native question parks only this task, releases its claims/batch and returns deferred=true: exit; the supervisor continues other ready work. Never poll the human or mark waiting work Done. Human answers reopen the task natively, subject to Stop/quota. Never auto-answer permission callbacks or fabricate physical reviews.')


def batch_prompt(prompt,batch,client,code):
    """One live claim, one CLI context, no contradictory single-task exit."""
    prompt=prompt.replace('Complete all acceptance, verify, work_finish(close_session=true), exit; the supervisor will continue with the next eligible task.',
                          'Complete and verify each task separately, preserving this CLI context until the batch finishes.')
    prompt=prompt.replace('Claim an eligible task/files (claimed=true) ONLY for '+str(code),'Work sequentially starting with '+str(code))
    return prompt+'\nSequential reserved batch headers (bounded; load only the current scope): '+json.dumps(batch,separators=(',',':'))+'. Others remain TODO and reserved, not In Progress. Finish each with work_finish(use_batch=true,claim_next=true,client="'+client+'"). Continue only with returned next.task and next.claim; a successful next.claim already grants its fresh token and full scope, so do not start/claim/read it again. A missing/failed next.claim requires task_get and task_update(claim=true) or the normal MCP fallback before edits. Exit when batch_done, session offline, Stop/quota or a real impediment; never select outside this batch yourself.'

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
        core={'scrum-work'}
        for base in [home/'skills',Path.home()/'.agents/skills']:
            for path in sorted(base.glob('scrum-*')):
                if path.name not in core and (path/'SKILL.md').is_file() and os.path.normcase(str(path.resolve())) not in known:
                    entries.append({'path':str(path),'enabled':False})
        value='['+','.join('{path='+json.dumps(item['path'])+',enabled='+str(bool(item.get('enabled',True))).lower()+'}' for item in entries)+']'
        return ['-c','skills.config='+value] if entries else []
    except (OSError,ValueError,KeyError,TypeError):return []

def command(client,folder,prompt,model='',policy_folder=None,coordination_channel='cli'):
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
        if coordination_channel=='cli':
            import storage
            rules={**rules,storage.registry_path().parent.as_posix():'write',(Path(policy_folder or folder)/'.scrum').as_posix():'write',**native_git_filesystem(policy_folder or folder)}
        options+=['-c','permissions.scrum-worker.filesystem={'+','.join(json.dumps(path)+'='+json.dumps(mode) for path,mode in rules.items())+'}']
        if coordination_channel=='cli':
            options+=['-c','mcp_servers.scrum-studio.enabled=false']
        else:
            options+=['-c','mcp_servers.scrum-studio.tool_timeout_sec=180','-c','mcp_servers.scrum-studio.enabled=true','-c','mcp_servers.scrum-studio.command='+json.dumps(sys.executable),'-c','mcp_servers.scrum-studio.args='+json.dumps([str(ROOT/'mcp_server.py'),'--worker-schema','--execution-schema']),'-c','mcp_servers.scrum-studio.enabled_tools='+json.dumps(SCRUM_WORK_TOOLS),'-c','mcp_servers.scrum-studio.default_tools_approval_mode="approve"']
        return [executable,*options,'exec','--ephemeral','--json','--skip-git-repo-check','--cd',str(folder),*model_args,'-']
    # Claude CLI: same worker catalog as Codex. A config file (not inline JSON)
    # survives Windows .cmd quoting. Preserve configured project MCP servers
    # (for example Unity or codebase-memory) instead of hiding required tools. Headless -p cannot
    # answer permission prompts, so the Scrum tools and workspace edits are
    # pre-approved explicitly; nothing bypasses permissions.
    config,strict=claude_mcp_config(policy_folder or folder,with_mode=True,include_scrum=coordination_channel!='cli')
    return [executable,'-p','Follow the Scrum task instructions supplied on stdin.','--output-format','stream-json','--verbose',*model_args,
            '--mcp-config='+str(config),*(['--strict-mcp-config'] if strict else []),'--permission-mode=acceptEdits',
            '--allowedTools='+','.join(CLAUDE_WORK_TOOLS+tuple('mcp__scrum-studio__'+name for name in SCRUM_WORK_TOOLS if coordination_channel!='cli'))]

CLAUDE_WORK_TOOLS=('Read','Edit','Write','Glob','Grep','Bash(python:*)','Bash(python3:*)','Bash(py:*)','Bash(git status:*)','Bash(git diff:*)')

def claude_mcp_config(folder=None,with_mode=False,include_scrum=True):
    import mcp_selection
    folder=Path(folder) if folder is not None else ROOT
    servers,strict=mcp_selection.claude_config(folder,{'type':'stdio','command':sys.executable,'args':[str(ROOT/'mcp_server.py'),'--worker-schema','--execution-schema']},include_scrum=include_scrum)
    path=folder/('.scrum/local/claude-worker-mcp.json' if include_scrum else '.scrum/local/claude-cli-mcp.json')
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
        # Explicit Standard deliberately separates session startup from its
        # claim. The supervisor can perform that normal claim after reviewing
        # its returned ownership, rather than treating the deferral as a Stop.
        locks=result.get('own_file_locks')
        if (not result.get('checkpoint',{}).get('should_stop')
                and result.get('claim_deferred')=='Standard workflow required: Standard selected by project policy'
                and isinstance(locks,dict) and not locks.get('items') and not locks.get('has_more')
                and not result.get('active_tasks')):
            result['claim']=rpc.call('task_claim',{'project':project,'actor':actor,'id':task})
            if result['claim'].get('claimed'):
                result['task']=rpc.call('task_get',{'project':project,'id':task})
                result.pop('claim_deferred',None)
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
    return {'task':result['task'],'token':claim['token'],'measurement_session':result.get('measurement_session'),**{k:claim[k] for k in ('dirty_planned','git','file_lock_mode') if k in claim}}

def preclaimed_prompt(started):
    task=started['task']
    return ('\nThe supervisor already ran work_start and claimed '+str(task.get('code'))+' for you (In Progress). Do NOT call work_start or task_claim again, or repeat task_get for the same scope. Pending task reminders may still be fetched on demand.'
            ' Its token is '+json.dumps(started['token'])+'; pass it to task_update/work_finish and never print or log it.'
            ' Claim diagnostics: '+json.dumps({k:started[k] for k in ('dirty_planned','git') if k in started},separators=(',',':'))+
            ' Full task (acceptance criteria, checklist, files, evidence, continuity): '+json.dumps(task,ensure_ascii=False,separators=(',',':'))+
            '\nIf continuity is present, resume from it and never redo recorded work. Edit, verify, then finish in one call: work_finish with evidence and continuity if anything remains.'
            ' Quota is supervised outside the model; call agent_checkpoint only to pause or when a long step ends.')+file_scope_guidance(started)

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
    rpc=ScrumClient(channel='supervisor')
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
        pending_usage_resume=False
        usage_request=None
        pending_auto=False
        recovery_attempts=set()
        checkout_attempts=set()
        incomplete_attempts={};pending_recovery=None
        capacity_attempts={};capacity_pending=None
        preflight_attempts={}
        checkout_watch=None;checkout_seen=None
        profile_generation=managed['generation'] if managed else 0
        last_usage=float('-inf');last_policy=0;policy={};observed={}
        last_checkpoint=None;previous_running=False;control_stamp=idle_signals.stamp(folder)
        cost_watch=None
        print('Bridge connected; press Wakeup in the project. Stop with Ctrl+C.',flush=True)
        while True:
            at=time.monotonic()
            signal_stamp=idle_signals.stamp(folder)
            signal_changed=signal_stamp!=control_stamp
            control_stamp=signal_stamp
            running=child is not None and child.poll() is None
            if checkout_watch is not None and child is None:
                checkout_now=idle_signals.checkout_stamp(folder,checkout_watch)
                if checkout_now!=checkout_seen:
                    checkout_seen=checkout_now
                    pending_start=True;signal_changed=True
            usage_ready=usage_future is not None and usage_future.done()
            capacity_due=capacity_pending is not None and at>=capacity_pending[0]
            pending_ready=(pending_start or pending_auto or capacity_due) and usage_future is None
            if not idle_signals.checkpoint_due(at,last_checkpoint,running,previous_running,signal_changed) and not (child is not None and not running) and not pending_ready and not usage_ready:
                time.sleep(2)
                continue
            last_checkpoint=at;previous_running=running
            if cost_watch:
                cost_watch.tick(active=running)
                if not running:cost_watch=None
            if not policy or signal_changed or at-last_policy>=300:
                policy=rpc.call('project_settings',{'project':args.project});last_policy=at
            if usage_future is not None and usage_future.done():
                try:
                    observed=usage_future.result()
                except Exception:
                    observed={}
                usage_future=None
            try:usage_measurement.observe(folder,observed,args.client)
            except (OSError,ValueError,KeyError):pass
            report=rpc.call('agent_checkpoint',dict(project=args.project,actor=actor,client=args.client,action='observe',running=running,**({'profile_id':args.profile} if args.profile else {}),**observed))
            request_key=(report['control']['generation'],report.get('profile_generation',profile_generation))
            requested=pending_start or request_key[0]>attempted or request_key[1]>profile_generation
            refresh_request=(requested and request_key!=usage_request and not running
                             and not report['control']['paused'] and report.get('profile_enabled',True))
            wants_usage=running or pending_start or pending_usage_resume or pending_auto or capacity_due or signal_changed or policy.get('auto_resume_session_quota',False) or not observed
            if usage_future is None and (refresh_request or wants_usage and at-last_usage>=policy.get('usage_check_seconds',60)):
                # A slow provider request must never block the local Stop loop.
                # Generation is authoritative: a missed/coalesced notification
                # must not consume Wakeup using a stale cached observation.
                usage_future=usage_pool.submit(usage_file,args.usage_file) if args.usage_file else usage_pool.submit(codex_usage) if args.client=='codex' else None
                if args.client=='claude' and not args.usage_file:
                    observed={}
                last_usage=at
                if refresh_request:usage_request=request_key
            actual=reported_model(log_path,log_offset) if log_path and selected_model else ''
            if actual and actual!=seen_model:
                seen_model=actual
                chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: requested model {selected_model}; CLI reported {actual}.')
            if child and model_mismatch(selected_model,actual):
                release_launch(rpc,args.project,actor)
                stop_child(child);output.close();output=None;child=None;running=False
                error=f'Requested model {selected_model}, but CLI reported {actual}. Run stopped; select an available exact model or an intentional provider alias. No replacement was selected by Scrum Studio.'
                chat_event(rpc,args.project,actor,error)
                usage_checkpoint(rpc,{'project':args.project,'actor':actor,'client':args.client,'action':'offline','reason':'Provider model mismatch','run_exit_code':-1,'run_error':error,'cli_usage_report':measured_report(folder,log_path,log_offset,args.client,run_id,selected_model,run_tasks),'summary':'Worker stopped after model mismatch; validate any partial edits before resuming.'})
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
                usage_checkpoint(rpc,{'project':args.project,'actor':actor,'client':args.client,'action':'offline','automatic':not bool(error) or str(report.get('reason','')).startswith('5h usage '),'reason':report['reason'] if report['should_stop'] and report['reason'].startswith('5h usage ') else 'CLI failed' if child.returncode else 'Task closure incomplete' if unfinished else 'CLI session ended','run_exit_code':child.returncode,'run_error':error,'cli_usage_report':measured_report(folder,log_path,log_offset,args.client,run_id,selected_model,released.get('batch_tasks',run_tasks)),'summary':summary})
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
                capacity_key=(offered_task,selected_model)
                delay=capacity_retry(error,capacity_attempts.get(capacity_key,0),report) if error and offered_task is not None else None
                if delay is not None:
                    capacity_attempts[capacity_key]=capacity_attempts.get(capacity_key,0)+1
                    capacity_pending=(time.monotonic()+delay,offered_task,selected_model)
                    chat_event(rpc,args.project,actor,f'Provider capacity error. Same-model retry {capacity_attempts[capacity_key]}/3 in {delay}s; Stop/quota checks still apply.')
                elif not error:
                    capacity_attempts.pop(capacity_key,None)
                child=None
            if running and report['should_stop']:
                stop_child(child)
                released=release_launch(rpc,args.project,actor) or {}
                output.close();output=None;child=None
                answer=final_message(log_path,log_offset)
                chat_event(rpc,args.project,actor,f'{args.client} worker {args.worker}: interrupted after Stop/quota signal. Task handoff and own lock release follow.'+('\nLast captured response (may predate interruption): '+answer if answer else ' No final response was captured; task handoff uses recorded progress.'))
                usage_checkpoint(rpc,{'project':args.project,'actor':actor,'client':args.client,'action':'pause','automatic':True,'reason':report['reason'],'summary':'','cli_usage_report':measured_report(folder,log_path,log_offset,args.client,run_id,selected_model,released.get('batch_tasks',run_tasks))})
            if report['should_stop'] and not running and str(report.get('reason','')).startswith(('5h usage ','weekly usage ')):
                finish_measurement(folder,report['reason'],rpc,args.project,args.client)
            if report['control']['paused'] and not running:
                finish_measurement(folder,'Project Stop',rpc,args.project,args.client)
            if managed and not report.get('profile_enabled',True):break
            requested=pending_start or report['control']['generation']>attempted or report.get('profile_generation',profile_generation)>profile_generation
            pending_usage_resume,usage_restart=usage_retry_intent(report,requested,pending_usage_resume,policy,usage_ready)
            if usage_restart:pending_start=True
            pending_auto=pending_auto or report.get('auto_resume',{}).get('requested',False)
            if report['control']['paused'] or report['should_stop'] or report.get('auto_resume',{}).get('status','').startswith(('disabled','cancelled')):
                pending_auto=False
            if report['control']['paused'] or report['should_stop']:
                # Keep the human's request while its one native quota read is
                # still pending. Failure/high quota/Stop do not preserve it.
                if not waiting_for_fresh_usage(report,usage_future is not None):pending_start=False
                checkout_watch=None;checkout_seen=None;capacity_pending=None
            current=report['control']['generation']
            profile_request=report.get('profile_generation',profile_generation)>profile_generation
            recovery_id=report.get('git_recovery_id')
            recovery_pending=recovery_id is not None and recovery_id not in recovery_attempts and not report['should_stop']
            immediate=(pending_start or profile_request or pending_auto or recovery_pending or capacity_due) and usage_future is None
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
                        wanted=capacity_pending[1] if capacity_pending else pending_recovery or recovery_id
                        pending_recovery=None
                        offer=rpc.call('worker_launch',dict(project=args.project,actor=actor,action='reserve',client=args.client,**({'id':wanted} if wanted else {})))
                        preflight_attempts.pop(current,None)
                        if recovery_id:recovery_attempts.add(recovery_id)
                    except RuntimeError as error:
                        capacity_pending=None
                        attempt=preflight_attempts.get(current,0)
                        delay=preflight_transport_delay(error,attempt)
                        # The offer grants no editing token. Its acknowledged
                        # release reconciles a lost response before any retry.
                        released=release_launch(rpc,args.project,actor) if delay is not None else None
                        if isinstance(released,dict) and 'released' in released:
                            preflight_attempts[current]=attempt+1
                            pending_start=True;pending_recovery=wanted
                            print(f'Launch offer transport recovered; native retry {attempt+1}/2 in {delay}s. No model started.',flush=True)
                            time.sleep(delay)
                            continue
                        print('Wakeup preflight unavailable; no model started.',flush=True)
                        chat_event(rpc,args.project,actor,'Wakeup preflight failed; no model started: '+str(error)[:500])
                        time.sleep(2)
                        continue
                    if not offer.get('eligible'):
                        if str(offer.get('reason','')).startswith('No eligible'):
                            finish_measurement(folder,'No eligible work remains',rpc,args.project,args.client)
                        capacity_pending=None
                        if offer.get('checkout_recovery'):
                            checkout_watch=offer['checkout_recovery'].get('files',[])[:100]
                            checkout_seen=idle_signals.checkout_stamp(folder,checkout_watch)
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
                    if capacity_pending:
                        retry_model=offer.get('selection',{}).get('model',report.get('profile_model',managed.get('model','') if managed else ''))
                        expected_model=capacity_pending[2]
                        capacity_pending=None
                        if retry_model!=expected_model:
                            release_launch(rpc,args.project,actor)
                            chat_event(rpc,args.project,actor,'Capacity retry cancelled: model selection changed; send Wakeup to authorize the new selection.')
                            continue
                    checkout_watch=None;checkout_seen=None
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
                                checkout_watch=state[1][:100];checkout_seen=idle_signals.checkout_stamp(folder,checkout_watch)
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
                        prompt=execution_prompt(args.project,folder,actor,args.client,offer,policy,role,automatic,bool(recovery_id))+preclaimed_prompt(started)
                    if started.get('task',{}).get('category')=='Git recovery':
                        prompt=(f'Use scrum-work for the existing scoped Git recovery in project {args.project}, actor {actor}. '
                            'The supervisor already claimed it. Preserve unrelated work and staging, honor recorded branch parents, '
                            'never force push or discard work. Read existing native Git reports and retained verification first; '
                            'do not repeat implementation or unchanged Unity/tests. Test only code actually changed by conflict resolution. '
                            'Complete the recorded recovery, publish normally, work_finish(close_session=true), and exit. '
                            'For an unresolved concrete constraint save precise continuity; an unidentified owner alone is not a blocker. '
                            'Respect Stop/quota/reservations and real permission review. '
                            f'Python: {worker_python()}; MCP fallback: {ROOT / "scripts/mcp_client.py"}. '+preclaimed_prompt(started))
                    channel=policy.get('coordination_channel','cli')
                    if channel=='cli':prompt=cli_prompt(args.project,folder,actor,args.client,offer,policy,started,role)
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
                        worker_env={**os.environ,'SCRUM_BRIDGE_WORKER':'1'}
                        if channel=='cli':
                            from scrum_cli import create_context
                            context_path=folder/'.scrum/local/cli-context'/f'{run_id}.json'
                            create_context(context_path,args.project,actor,args.client,started,batch_run)
                            worker_env['SCRUM_CLI_CONTEXT']=str(context_path)
                        with tempfile.TemporaryFile(mode='w+b') as prompt_input:
                            prompt_input.write(prompt.encode('utf-8'));prompt_input.seek(0)
                            launch=interactive_command(command(args.client,run_folder,prompt,selected_model,policy_folder=folder,coordination_channel=channel),args.client,args.project,actor,run_folder,selected_model,policy)
                            cost_path=Path(folder)/'.scrum/local/run-cost'/f'{actor}.json'
                            child=subprocess.Popen(launch,cwd=run_folder,stdin=prompt_input,stdout=output,stderr=subprocess.STDOUT,env={**worker_env,'SCRUM_RUN_COST_STATE':str(cost_path)},**options)
                            cost_watch=run_cost.Watch(log_path,log_offset,cost_path,run_id,run_tasks,policy,args.client)
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
        if 'cost_watch' in locals() and cost_watch:cost_watch.tick(active=False)
        if output:output.close()
        if actor:
            release_launch(rpc,args.project,actor)
            try:
                usage_checkpoint(rpc,{'project':args.project,'actor':actor,'client':args.client,'action':'offline','summary':'Bridge disconnected. Resume from task history; validate partial edits.',**({'cli_usage_report':measured_report(folder,log_path,log_offset,args.client,run_id,selected_model,run_tasks)} if run_id and log_path else {})})
            except RuntimeError:
                pass
        if 'folder' in locals():finish_measurement(folder,'Supervisor disconnected',rpc,args.project,args.client)
        rpc.close()
        usage_pool.shutdown(wait=False,cancel_futures=True)

if __name__=='__main__':
    main()
