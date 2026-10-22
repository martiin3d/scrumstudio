"""Private local checkpoints for blocked work in a single checkout; no inference."""
import hashlib
import json
import os
from pathlib import Path
import uuid

import git_sync as git


def folder(root, branch):
    root=Path(root).resolve()
    path=root/'.scrum/local/git-paused'/hashlib.sha256(branch.encode()).hexdigest()
    if not path.resolve().is_relative_to(root):raise ValueError('Checkpoint storage must stay inside the project')
    return path


def save(path, value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def checked(root, *args):
    result=git._git(root,*args,timeout=20)
    if result.returncode:raise ValueError('Local checkpoint: '+(result.stderr or result.stdout).strip()[-300:])
    return result.stdout.strip()


def paths(root):
    # NUL delimiters preserve spaces, quotes and rename endpoints.
    # Do not strip a NUL-delimited pathname list.
    result=git._git(root,'diff','--no-renames','--name-only','-z','HEAD','--','.',':!.scrum')
    untracked=git._git(root,'ls-files','--others','--exclude-standard','-z','--','.',':!.scrum')
    if result.returncode or untracked.returncode:raise ValueError('Cannot inspect checkpoint files')
    return set(result.stdout.rstrip('\0').split('\0'))-{''},set(untracked.stdout.rstrip('\0').split('\0'))-{''}


def safe_path(root, name):
    path=Path(root)/name
    if Path(name).is_absolute() or any(p in ('.git','.scrum','..') for p in Path(name).parts):
        raise ValueError('Unsafe checkpoint path')
    if not path.resolve().is_relative_to(Path(root).resolve()) or path.is_symlink() or path.is_dir() or any(p.is_symlink() for p in path.parents):
        raise ValueError('Checkpoint requires regular files inside the checkout')
    return path


def pause(root, branch, planned, tasks):
    """Caller holds the checkout lock and has excluded live claims/reservations.

    Unknown edits and staging are deliberately refused. They need actual ownership
    reconciliation, never automatic publication or a blanket stash.
    """
    current=git.state(root)
    if current is None or current[0]!=branch:return None
    if Path(checked(root,'rev-parse','--show-toplevel')).resolve()!=Path(root).resolve():return None
    directory=folder(root,branch);manifest=directory/'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text())['state']!='restored':return None
    if git._git(root,'diff','--cached','--quiet').returncode!=0:return None
    if git._git(root,'ls-files','--unmerged').stdout.strip():return None
    if git._git(root,'diff','--quiet','HEAD','--','.scrum').returncode!=0:return None
    tracked,untracked=paths(root);names=tracked|untracked
    planned={Path(name).as_posix() for name in planned}
    if not names or len(names)>1000 or not names.issubset(planned):return None
    # Bound local copying; never scan ignored Unity caches or generated libraries.
    files={name:safe_path(root,name) for name in names}
    if sum(path.stat().st_size for path in files.values() if path.exists())>32*1024*1024:return None
    directory=directory/uuid.uuid4().hex;directory.mkdir(parents=True)
    data={'branch':branch,'base':checked(root,'rev-parse','HEAD'),'tasks':tasks,
          'state':'preparing','files':{},'ref':None}
    for index,(name,path) in enumerate(sorted(files.items())):
        raw=path.read_bytes() if path.exists() else None
        entry={'blob':str(index),'sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None,
               'untracked':name in untracked,'mode':path.stat().st_mode if raw is not None else None}
        if raw is not None:
            with (directory/str(index)).open('wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
        data['files'][name]=entry
    # stash create writes objects without changing files, the index or refs/stash.
    if tracked:
        sha=checked(root,'stash','create','SCRUM private blocked work')
        if not sha:raise ValueError('Checkpoint did not capture tracked edits')
        ref='refs/scrum/paused/'+uuid.uuid4().hex
        checked(root,'update-ref',ref,sha,'0'*len(sha));data['ref']=ref
    data['state']='saved';save(directory/'manifest.json',data)
    # Recheck bytes and index before any removal; do not overwrite a human race.
    for name,entry in data['files'].items():
        path=safe_path(root,name);raw=path.read_bytes() if path.exists() else None
        if (hashlib.sha256(raw).hexdigest() if raw is not None else None)!=entry['sha256']:
            raise ValueError('Files changed during checkpoint; originals preserved')
    if git._git(root,'diff','--cached','--quiet').returncode!=0:
        raise ValueError('Staging changed during checkpoint; originals preserved')
    if checked(root,'rev-parse','HEAD')!=data['base'] or git.state(root)[0]!=branch or paths(root)!=(tracked,untracked):
        raise ValueError('Checkout changed during checkpoint; originals preserved')
    # Pointer is durable before cleaning: a process crash cannot orphan the backup.
    save(manifest,{'state':'cleaning','directory':directory.name})
    if tracked:checked(root,'restore','--source=HEAD','--worktree','--',*sorted(tracked))
    for name in untracked:safe_path(root,name).unlink()
    # Windows CRLF/stat entries can remain falsely dirty after restore. Re-add
    # only the restored paths, then require an unchanged index tree.
    if tracked:checked(root,'add','-u','--',*sorted(tracked))
    if git._git(root,'diff','--cached','--quiet').returncode!=0:
        raise ValueError('Restored files differ from HEAD; backup retained')
    if git.state(root)!=(branch,[]):raise ValueError('Checkpoint cleanup incomplete; backup retained')
    save(manifest,{'state':'paused','directory':directory.name})
    return {'branch':branch,'tasks':tasks,'files':len(names)}


def pending(root, branch):
    manifest=folder(root,branch)/'manifest.json'
    return manifest.exists() and json.loads(manifest.read_text())['state']!='restored'


def any_pending(root):
    if not root:return False
    directory=folder(root,'').parent
    return any(json.loads(path.read_text())['state']!='restored' for path in directory.glob('*/manifest.json'))


def restore(root, branch, task_code=None, allowed=True):
    """Restore only once, on the saved branch, before handing work to the model."""
    manifest=folder(root,branch)/'manifest.json'
    if not manifest.exists():return None
    pointer=json.loads(manifest.read_text())
    if pointer['state']=='restored':return None
    if not allowed:raise ValueError('Finish active work before restoring a local checkpoint')
    if pointer['state']!='paused':raise ValueError('Interrupted local checkpoint needs reconciliation; backup retained')
    key=pointer['directory']
    if len(key)!=32 or any(c not in '0123456789abcdef' for c in key):raise ValueError('Invalid checkpoint location')
    directory=manifest.parent/key;data=json.loads((directory/'manifest.json').read_text())
    if task_code and task_code not in data['tasks']:
        raise ValueError('Branch has saved edits for another task; resume its blocked task first')
    if git.state(root)!=(branch,[]):raise ValueError('Cannot restore blocked work into a dirty checkout')
    for name,entry in data['files'].items():
        path=safe_path(root,name)
        if entry['sha256'] is not None:
            raw=(directory/entry['blob']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Checkpoint backup failed verification')
        if entry['untracked'] and path.exists():raise ValueError('Checkpoint would overwrite a new file')
    save(manifest,dict(pointer,state='restoring'))
    if data['ref']:
        if not data['ref'].startswith('refs/scrum/paused/'):raise ValueError('Invalid private checkpoint ref')
        checked(root,'stash','apply',data['ref'])
    same_base=checked(root,'rev-parse','HEAD')==data['base']
    for name,entry in data['files'].items():
        # Keep Git's three-way result if the base advanced; preserve original raw
        # bytes (including CRLF/binary files) when returning to the original base.
        if not entry['untracked'] and not same_base:continue
        path=safe_path(root,name)
        if entry['sha256'] is None:path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((directory/entry['blob']).read_bytes());os.chmod(path,entry['mode'])
    save(manifest,dict(pointer,state='restored'))
    return {'branch':branch,'tasks':data['tasks'],'files':len(data['files'])}
