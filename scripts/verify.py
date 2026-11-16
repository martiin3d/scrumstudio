"""Plan proportional checks; --run executes the selected checks once."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
import hashlib
import shutil
import time

ROOT=Path(__file__).resolve().parents[1]
TARGETS={
    'agent_interactions.py':['test_interactions.py','test_autonomy.py'],
    'scripts/visual_compare.py':['test_visual_compare.py'],
    'file_plan_repair.py':['test_file_plan_repair.py'],
    'scrum_cli.py':['test_scrum_cli.py','test_scrum_cli_git.py'],
    'coordination_api.py':['test_mcp_protocol.py','test_scrum_cli.py'],
    'git_index.py':['test_git_fingerprints.py','test_git_native_recovery.py'],
    'git_completion.py':['test_git_fingerprints.py','test_git_completion_recovery.py','test_git_ignored_evidence.py','test_file_plan_capacity.py'],
    'git_sync.py':['test_git_fingerprints.py','test_git_native_recovery.py','test_git_completion_recovery.py','test_git_paused.py'],
    'workflow.py':['test_workflow.py','test_efficiency.py'],
    'overhead.py':['test_overhead.py','test_efficiency.py'],
    'telemetry.py':['test_telemetry.py'],
    'scheduling.py':['test_scheduling.py','test_batches.py'],
    'usage_audit.py':['test_usage_audit.py','test_execution_usage.py'],
    'execution_usage.py':['test_execution_usage.py','test_usage_audit.py'],
    'usage_measurement.py':['test_usage_measurement.py','test_usage_audit.py'],
    'worker_catalog.py':['test_bridge.py','test_usage_measurement.py'],
    'context_batches.py':['test_batches.py','test_scheduling.py','test_workflow.py'],
    'scripts/measure_usage.py':['test_usage_measurement.py'],
    'run_cost.py':['test_run_cost.py','test_bridge.py','test_usage_audit.py'],
    'scripts/work_checks.py':['test_work_checks.py'],
    'context_tools.py':['test_context_tools.py','test_efficiency.py','test_workflow.py'],
    'scripts/unity_verify.py':['test_unity_verify.py'],
    'scripts/verification_receipts.py':['test_unity_verify.py'],
    'scripts/verify.py':['test_context_tools.py','test_work_checks.py'],
    'scripts/setup.py':['test_setup.py'],
    'scripts/agent_bridge.py':['test_bridge.py'],
    'scripts/eval_worker.py':['test_eval_worker.py'],
    'wiki_knowledge.py':['test_wiki_knowledge.py'],
    'scripts/wiki_doctor.py':['test_wiki_knowledge.py'],
    'scripts/provider_usage.py':['test_bridge.py','test_overhead.py'],
    'scripts/usage_monitor.py':['test_overhead.py'],
}

IGNORED={'.git','.scrum','data','__pycache__','node_modules','.venv','.pytest_cache'}

def fingerprint(root=ROOT):
    """Conservative tree hash; unavailable/oversized inputs disable reuse."""
    digest=hashlib.sha256();count=total=0
    def fail(error):raise error
    try:
        for folder,dirs,files in os.walk(root,onerror=fail):
            dirs[:]=sorted(d for d in dirs if d not in IGNORED)
            if any((Path(folder)/d).is_symlink() for d in dirs):return None
            for name in sorted(files):
                path=Path(folder)/name
                if path.is_symlink():return None
                count+=1;total+=path.stat().st_size
                if count>5000 or total>50*1024*1024:return None
                digest.update(path.relative_to(root).as_posix().encode());digest.update(b'\0')
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()
    except OSError:return None

def signature(selected,source):
    node=shutil.which('node') if selected['node_syntax'] else None
    try:node_stamp=Path(node).stat().st_mtime_ns if node else None
    except OSError:node_stamp=None
    value=[source,sys.version,sys.executable,node,node_stamp,selected['tests'],selected['node_syntax']]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()

def read_cached(path,key):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if value.get('signature')==key and value.get('passed') is True else None
    except (OSError,ValueError,AttributeError):return None

def save_pass(path,key,source):
    import tempfile
    path.parent.mkdir(parents=True,exist_ok=True)
    data={'signature':key,'source':source,'passed':True,'verified_at':time.time()}
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as saved:
        json.dump(data,saved);temporary=saved.name
    os.replace(temporary,path)


RISK_CHECKS = {
    'schema': ('Persisted schema or migration changed.', ('test_storage.py', 'test_mcp_protocol.py')),
    'permissions': ('Authorization, claims, provider approvals, or Stop policy changed.',
                    ('test_policy.py', 'test_launch.py', 'test_single_worker_files.py')),
    'git_delivery': ('Git verification, integration, publication, or recovery changed.',
                     ('test_git_native_recovery.py', 'test_git_completion_recovery.py', 'test_worktrees.py')),
    'shared_contract': ('An operation or shared workflow contract changed.',
                        ('test_mcp_protocol.py', 'test_scrum_cli.py', 'test_workflow.py')),
}

RISK_PATHS = {
    'schema': {'storage.py'},
    'permissions': {'runtime.py'},
    'git_delivery': {'git_sync.py', 'git_completion.py', 'git_worktrees.py', 'git_retained.py', 'git_paused.py'},
    'shared_contract': {'coordination_api.py', 'mcp_server.py'},
}

RISK_CONTENT = {
    'schema': re.compile(r'\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX)\b|\b(?:user_version|schema_version|add_column)\b', re.I),
    'permissions': re.compile(r'\b(?:actor_user|require_claim|verify_token|lock_until|claim_token|should_stop|usage_guard|approval_policy|agent_approval_mode|sandbox_mode)\b'),
    'git_delivery': re.compile(r'\b(?:git_finalize|git_reconcile|reconcile_pr|completion_basis|publish_verified|merge_completed|save_receipt|verified_source_sha)\b'),
    'shared_contract': re.compile(r'\b(?:startup_task|agent_task|completion_basis|work_finish|work_start)\b'),
}


def diff_lines(name, base, root=None):
    root=ROOT if root is None else root
    args=['git','--literal-pathspecs','-C',str(root)]
    try:
        result=subprocess.run([*args,'diff','--no-ext-diff','--no-textconv','--unified=0',base,'--',name],
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)
        if result.returncode:return None
        changed=[line[1:] for line in result.stdout.splitlines()
                 if line.startswith(('+','-')) and not line.startswith(('+++ ','--- '))]
        if result.stdout:return changed
        tracked=subprocess.run([*args,'ls-files','--error-unmatch','--',name],capture_output=True,timeout=5)
        path=root/name
        if tracked.returncode and path.is_file():
            with path.open('rb') as stream:
                content=stream.read(256*1024+1)
            if len(content)>256*1024:return None
            return content.decode('utf-8',errors='replace').splitlines()
        return []
    except (OSError,subprocess.SubprocessError):
        return None


def plan(files,base='HEAD'):
    if not base or base.startswith('-'):
        raise ValueError('Supply a Git base revision')
    checks=set();notes=[];full=False;js=False;wiki=False
    risk={key:set() for key in RISK_CHECKS}
    observed={key:set() for key in RISK_CHECKS}
    unavailable=[]
    covered=[]
    for value in dict.fromkeys(files):
        path=(ROOT/value).resolve()
        try:name=path.relative_to(ROOT).as_posix()
        except ValueError:raise ValueError('Files must be inside this checkout') from None
        if name=='AGENTS.md' or (name.startswith('docs/agents/') and name.endswith('.md')):
            wiki=True
        if name=='public/app.js':js=True;notes.append('Verify the affected web flow in a browser.')
        elif name.startswith('tests/test_') and name.endswith('.py'):checks.add(Path(name).name)
        elif name in TARGETS:checks.update(TARGETS[name])
        elif name.endswith('.md') or name in ('public/style.css','public/index.html'):
            notes.append('Inspect documentation/skill references or the affected visual flow; no model-output matching tests.')
        else:full=True
        if not name.endswith(('.py','.js','.sql')) or name.startswith('tests/'):continue
        lines=diff_lines(name,base)
        if lines is None:unavailable.append(name)
        else:covered.append(name)
        for key,pattern in RISK_CONTENT.items():
            if name in RISK_PATHS[key] or (lines and any(pattern.search(line) for line in lines)):
                risk[key].add(name)
                if lines:observed[key].add(name)
    detected=[]
    for key,(reason,tests) in RISK_CHECKS.items():
        if risk[key]:
            checks.update(tests)
            detected.append({'area':key,'paths':sorted(risk[key]),'observed_paths':sorted(observed[key]),
                             'reason':reason,'checks':list(tests)})
    return {'tests':['test_*.py'] if full else sorted(checks),'node_syntax':js,'wiki_doctor':wiki,'notes':sorted(set(notes)),
            'risk':detected,'diff_base':base,'diff_unavailable':sorted(set(unavailable)),'diff_covered':covered,
            'reason':'Cross-cutting or unmapped change: run full regressions.' if full else 'Targeted checks; expand for new failures, broader impact or unresolved risk.'}


def risk_state(previous, selected):
    """Advance observations only for paths whose diff was actually available."""
    covered=set(selected['diff_covered'])
    observed={(risk['area'],path):{'area':risk['area'],'path':path,'reason':risk['reason'],'checks':risk['checks']}
              for risk in selected['risk'] for path in risk['observed_paths']}
    known={tuple(item) for item in previous.get('known',[])}
    introduced=[item for key,item in observed.items() if key not in known]
    retained=known-{key for key in known if key[1] in covered and key not in observed}
    pending=[item for item in previous.get('pending',[])
             if item['path'] not in covered or (item['area'],item['path']) in observed]
    unknown=sorted((set(previous.get('unknown',[]))-covered)|set(selected['diff_unavailable']))
    return {'base':selected['diff_base'],'known':[list(key) for key in sorted(retained|observed.keys())],
            'pending':pending+introduced,'unknown':unknown},introduced


def claim_risk_plan(files, requested_base, context):
    from context_tools import verification_risk_path, verification_risk_report, verification_risk_state
    from scrum_cli import atomic, context_file, locked
    import git_sync

    default=requested_base or 'HEAD'
    try:
        with locked(context):
            current=context_file(context).get('current')
            if not current:return plan(files,default),None
            task_id=current['id']
            previous=verification_risk_state(ROOT,task_id)
            base=requested_base or previous.get('base') or default
            if not isinstance(base,str) or not base or base.startswith('-'):
                raise ValueError('Supply a Git base revision')
            result=git_sync._git(ROOT,'rev-parse','--verify','--end-of-options',base+'^{commit}')
            base=result.stdout.strip() if not result.returncode else base
            selected=plan(files,base)
            if previous.get('base')!=base:previous={}
            state,introduced=risk_state(previous,selected)
            if introduced or state['pending'] or state['unknown']:
                selected['risk_change']={**verification_risk_report(state),'introduced':introduced[:12]}
            path=verification_risk_path(ROOT,task_id)
            if state!=previous:atomic(path,state)
            return selected,(context,task_id,state)
    except (OSError,ValueError,subprocess.SubprocessError,KeyError,TypeError,AttributeError):
        return plan(files,default),None


def record_verified_risk(checkpoint, selected):
    from context_tools import verification_risk_path, verification_risk_state
    from scrum_cli import atomic, context_file, locked

    if checkpoint is None:return
    context,task_id,saved=checkpoint
    if not saved['pending']:return
    eligible={(risk['area'],path) for risk in selected['risk'] for path in risk['observed_paths']}
    try:
        with locked(context):
            current=context_file(context).get('current')
            state=verification_risk_state(ROOT,task_id)
            if current and current['id']==task_id and state==saved:
                state['pending']=[item for item in state['pending'] if (item['area'],item['path']) not in eligible]
                if state!=saved:atomic(verification_risk_path(ROOT,task_id),state)
    except (OSError,ValueError,KeyError):
        pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files',nargs='+',required=True)
    parser.add_argument('--base',help='Compare with this Git revision (default: the current task verification base or HEAD)')
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--verbose',action='store_true',help='Stream unittest details instead of the default local report and compact result')
    parser.add_argument('--reuse',action='store_true',help='Reuse an actual prior pass only for identical sources, runtime and checks; report its original time')
    args=parser.parse_args()
    sys.path.insert(0,str(ROOT))
    native_context=os.getenv('SCRUM_CLI_CONTEXT')
    try:selected,checkpoint=claim_risk_plan(args.files,args.base,native_context) if native_context else (plan(args.files,args.base or 'HEAD'),None)
    except ValueError as error:parser.error(str(error))
    print(json.dumps(selected),flush=True)
    if not args.run:return
    if selected['wiki_doctor']:
        check=subprocess.run([sys.executable,str(ROOT/'scripts/wiki_doctor.py')],check=False)
        if check.returncode:raise SystemExit(check.returncode)
    source=fingerprint()
    key=signature(selected,source)
    import verification_receipts as receipts
    checks={}
    if native_context and selected['tests']:
        label='Python: '+', '.join(selected['tests'])
        checks[label]=(None,sys.executable,receipts.check_inputs(ROOT,None,sys.executable,tree=source) if source else None)
    if native_context and selected['node_syntax']:
        node=shutil.which('node')
        checks['JavaScript syntax']=(['public/app.js'],node,receipts.check_inputs(ROOT,['public/app.js'],node))
    cache=ROOT/'data'/'verification-cache'/('check-'+hashlib.sha256(json.dumps([selected['tests'],selected['node_syntax']]).encode()).hexdigest()[:20]+'.json')
    cached=read_cached(cache,key) if args.reuse and source else None
    if cached:
        record_verified_risk(checkpoint,selected)
        for label,(paths,executable,inputs) in checks.items():
            receipts.record_task_check(ROOT,label,paths,executable,inputs,inputs,cached['verified_at'])
        receipts.event(ROOT,key,{'passed':True,'kind':'passed','reused':True},0)
        print(json.dumps({'reused':True,'original_verified_at':cached['verified_at'],'source':source,'checks':selected['tests'],'node_syntax':selected['node_syntax'],'evidence':'Reused prior passing checks for identical source tree, runtime and selected tests; original timestamp retained.'}),flush=True)
        return
    os.chdir(ROOT);sys.path.insert(0,str(ROOT/'tests'))
    suite=unittest.TestSuite()
    for pattern in selected['tests']:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern))
    passed=True;executed=0;started=time.monotonic()
    if selected['tests']:
        report=ROOT/'data/verification-reports'/('checks-'+str(time.time_ns())+'.log')
        report.parent.mkdir(parents=True,exist_ok=True)
        with report.open('w',encoding='utf-8') as stream:
            if args.verbose:
                result=unittest.TextTestRunner(stream=sys.stderr,verbosity=1).run(suite)
            else:
                with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
                    result=unittest.TextTestRunner(stream=stream,verbosity=1).run(suite)
        if not args.verbose:
            detail=report.read_text(encoding='utf-8')
            print(json.dumps({'report':str(report),'executed':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),**({'failure_excerpt':detail[-8000:]} if not result.wasSuccessful() else {})}),flush=True)
        executed=result.testsRun
        passed=result.wasSuccessful() and executed>0
    python_passed=passed
    node_passed=False
    if selected['node_syntax']:
        node_passed=subprocess.run(['node','--check','public/app.js'],check=False).returncode==0
        passed=node_passed and passed
    final_source=fingerprint() if source and (passed or checks) else None
    for label,(paths,executable,inputs) in checks.items():
        verified=python_passed if paths is None else node_passed
        after=receipts.check_inputs(ROOT,paths,executable,tree=final_source) if verified and (paths or final_source) else None
        receipts.record_task_check(ROOT,label,paths,executable,inputs,after,time.time())
    if passed and source and (selected['tests'] or selected['node_syntax']) and final_source==source:
        record_verified_risk(checkpoint,selected)
        try:save_pass(cache,key,source)
        except OSError:print('Verification passed; cache unavailable.',file=sys.stderr)
    if selected['tests'] or selected['node_syntax']:
        receipts.event(ROOT,key if source else None,{'passed':passed,'kind':'passed' if passed else 'check_failure','reused':False,'executed':executed},time.monotonic()-started)
    print(json.dumps({'passed':passed,'reused':False,'tests_selected':selected['tests'],'evidence':'Selected checks passed.' if passed else 'Selected checks failed; inspect the concrete failure above.'}),flush=True)
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()
