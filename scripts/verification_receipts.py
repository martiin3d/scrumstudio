"""Conservative local verification receipts and content-free audit events."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


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
