"""Conservative local verification receipts and content-free audit events."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def check_inputs(workspace, paths, executable, *, tree=None):
    """Fingerprint only declared inputs, including the tool binary identity."""
    from project_knowledge import safe_file, source_state

    if not executable:
        return None
    try:
        tool = Path(executable).resolve(strict=True)
        stamp = tool.stat()
        inputs = {'tool': [str(tool), stamp.st_size, stamp.st_mtime_ns]}
        if paths is None:
            from scripts.verify import fingerprint
            inputs['tree'] = tree if tree is not None else fingerprint(Path(workspace))
            return inputs if inputs['tree'] else None
        if not isinstance(paths, list) or not paths or not all(isinstance(name, str) for name in paths):
            return None
        for name in paths:
            path = safe_file(Path(workspace).resolve(), name)
            if not path:
                return None
            state = source_state(path)
            if not state:
                return None
            inputs[name] = state
        return inputs
    except OSError:
        return None


def task_check_path(workspace, task_id):
    return Path(workspace) / '.scrum/local/task-checks' / f'{int(task_id)}.json'


def read_task_checks(workspace, task_id):
    try:
        path = task_check_path(workspace, task_id)
        if path.is_symlink() or path.stat().st_size > 128 * 1024:
            return {}
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def record_task_check(workspace, check, paths, executable, before, after, verified_at):
    """Store a verified result in the current native claim's local checkout."""
    from scrum_cli import atomic, context_file, locked

    context = os.getenv('SCRUM_CLI_CONTEXT')
    if not context:
        return
    try:
        with locked(context):
            claim = context_file(context).get('current')
            if not claim:
                return
            existing = read_task_checks(workspace, claim['id'])
            existing.pop(check, None)
            existing[check] = {
                'paths': paths, 'executable': executable,
                'inputs': before if before and before == after else None,
                'verified_at': verified_at,
            }
            atomic(task_check_path(workspace, claim['id']), dict(list(existing.items())[-12:]))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return


def resume_checks(workspace, task_id):
    records = read_task_checks(workspace, task_id)
    if not records:
        return [{'status': 'unknown', 'reason': 'No scoped verification receipts in this checkout.'}]
    checks = []
    for name, receipt in list(records.items())[-12:]:
        result = {'check': name, 'status': 'unknown'}
        if not isinstance(receipt, dict) or not receipt.get('inputs'):
            result['reason'] = 'Receipt input coverage is incomplete.'
        else:
            current = check_inputs(workspace, receipt.get('paths'), receipt.get('executable'))
            if current is None:
                result['reason'] = 'A covered input or its tool is unavailable.'
            elif current != receipt['inputs']:
                result.update(status='changed', reason='Recorded verification inputs changed.')
            elif type(receipt.get('verified_at')) in (int, float):
                result.update(status='reusable', result='passed', verified_at=receipt['verified_at'])
            else:
                result['reason'] = 'The receipt has no verification timestamp.'
        checks.append(result)
    return checks


def local_root(project):
    project=Path(project).resolve()
    for root in (project,project.parent):
        if (root/'.scrum/local').is_dir():return root/'.scrum/local'
    return project/'.scrum/local'


def fingerprint(project,editor,platform,test_filter,graphics):
    root=Path(project);digest=hashlib.sha256();total=0;count=0
    editor=Path(editor)
    try:
        for script in ('unity_verify.py','verification_receipts.py'):
            digest.update((Path(__file__).parent/script).read_bytes())
        # External mutable local packages are not covered by this project tree.
        manifest=root/'Packages/manifest.json'
        if manifest.exists() and 'file:' in manifest.read_text(encoding='utf-8'):return None
        digest.update(json.dumps([str(root.resolve()),str(editor.resolve()),editor.stat().st_size,editor.stat().st_mtime_ns,platform,test_filter,graphics,os.name,os.environ.get('USERNAME') or os.environ.get('USER'),os.environ.get('UNITY_LICENSE_FILE')],sort_keys=True).encode())
        for name in ('Assets','Packages','ProjectSettings','Library/PackageCache'):
            directory=root/name
            if not directory.exists():continue
            def fail(error):raise error
            for folder,dirs,files in os.walk(directory,onerror=fail):
                dirs.sort()
                if any((Path(folder)/d).is_symlink() for d in dirs):return None
                for filename in sorted(files):
                    path=Path(folder)/filename
                    if path.is_symlink():return None
                    size=path.stat().st_size;total+=size;count+=1
                    if total>1024*1024*1024 or count>50000:return None
                    digest.update(path.relative_to(root).as_posix().encode());digest.update(b'\0')
                    with path.open('rb') as stream:
                        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return digest.hexdigest()
    except (OSError,ValueError):return None


def receipt_path(project,key):return local_root(project)/'verification-cache'/('unity-'+key+'.json')


def load(project,key):
    if not key:return None
    try:
        data=json.loads(receipt_path(project,key).read_text(encoding='utf-8'))
        if data.get('signature')!=key or not data.get('passed'):return None
        report=Path(data['report'])
        report.resolve().relative_to((Path(project)/'Logs').resolve())
        if hashlib.sha256(report.read_bytes()).hexdigest()!=data['report_hash']:return None
        return data
    except (OSError,ValueError,KeyError,TypeError):return None


def save(project,key,result):
    if not key or not result.get('passed'):return
    data=dict(result,signature=key,verified_at=time.time(),report_hash=hashlib.sha256(Path(result['report']).read_bytes()).hexdigest())
    path=receipt_path(project,key);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as stream:
        json.dump(data,stream);temporary=stream.name
    os.replace(temporary,path)


def event(project,signature,result,seconds):
    try:
        path=local_root(project)/'verification-events.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        # No output, commands, paths, secrets or model text in the audit event.
        data={k:result.get(k) for k in ('passed','kind','reused','tests','executed')}
        data.update(at=time.time(),signature=signature,seconds=round(seconds,3))
        with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(data)+'\n')
    except OSError:pass
