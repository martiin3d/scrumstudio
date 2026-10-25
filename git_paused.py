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


def pause(root, branch, planned, tasks, context_group=''):
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
          'state':'preparing','files':{},'ref':None,'context_group':context_group}
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
    for path_chunk in git.path_chunks(sorted(tracked)):checked(root,'restore','--source=HEAD','--worktree','--',*path_chunk)
    for name in untracked:safe_path(root,name).unlink()
    # Windows CRLF/stat entries can remain falsely dirty after restore. Re-add
    # only the restored paths, then require an unchanged index tree.
    for path_chunk in git.path_chunks(sorted(tracked)):checked(root,'add','-u','--',*path_chunk)
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


def restore(root, branch, task_code=None, allowed=True, context_group=''):
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
    if task_code and task_code not in data['tasks'] and not (context_group and context_group==data.get('context_group')):
        raise ValueError('Branch has saved edits for another task; resume its blocked task first')
    if git.state(root)!=(branch,[]):raise ValueError('Cannot restore blocked work into a dirty checkout')
    retained={}
    current_tip=checked(root,'rev-parse','HEAD')
    for name,entry in data['files'].items():
        path=safe_path(root,name)
        if entry['sha256'] is not None:
            raw=(directory/entry['blob']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Checkpoint backup failed verification')
        if entry['untracked'] and path.exists():
            # A completed prerequisite may have delivered this originally
            # untracked input. Keep its committed descendant version and the
            # old draft backup; never overwrite it with the parked draft.
            tracked=git._git(root,'ls-files','--error-unmatch','--',name).returncode==0
            if not tracked or not git.is_ancestor(root,data['base'],current_tip):
                raise ValueError('Checkpoint would overwrite a new file')
            retained[name]={'path':name,'saved_read_path':str(directory/entry['blob']),'saved_sha256':entry['sha256'],'reason':'Committed descendant input retained; saved draft remains available for comparison.'}

    save(manifest,dict(pointer,state='restoring'))
    if data['ref']:
        if not data['ref'].startswith('refs/scrum/paused/'):raise ValueError('Invalid private checkpoint ref')
        checked(root,'stash','apply',data['ref'])
    same_base=checked(root,'rev-parse','HEAD')==data['base']
    for name,entry in data['files'].items():
        # Keep Git's three-way result if the base advanced; preserve original raw
        # bytes (including CRLF/binary files) when returning to the original base.
        if name in retained or (not entry['untracked'] and not same_base):continue
        path=safe_path(root,name)
        if entry['sha256'] is None:path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((directory/entry['blob']).read_bytes());os.chmod(path,entry['mode'])
    save(manifest,dict(pointer,state='restored'))
    result={'branch':branch,'tasks':data['tasks'],'files':len(data['files'])}
    if retained:result['retained_upstream']=list(retained.values())
    return result


def preserved_inputs(root, code, context_group='', after=0, limit=8):
    """Read-only, verified pointers to parked inputs; never restore across branches.

    A prerequisite may need the draft of its blocked dependent in the same
    context. That draft can be untracked and therefore absent from Git history.
    """
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('Invalid preserved input cursor/limit')
    if not root:return None
    root=Path(root).resolve();base=folder(root,'').parent;items=[];limited=False;budget=64*1024*1024
    for index,pointer_path in enumerate(sorted(base.glob('*/manifest.json'))):
        if index>=100:limited=True;break
        try:
            if pointer_path.is_symlink() or not pointer_path.resolve().is_relative_to(root) or pointer_path.stat().st_size>10000:limited=True;continue
            pointer=json.loads(pointer_path.read_text(encoding='utf-8'))
            if pointer.get('state') not in ('paused','restored'):continue
            key=pointer.get('directory','')
            if not isinstance(key,str) or len(key)!=32 or any(c not in '0123456789abcdef' for c in key):limited=True;continue
            directory=pointer_path.parent/key;manifest=directory/'manifest.json'
            if directory.is_symlink() or pointer_path.parent.is_symlink():limited=True;continue
            if manifest.is_symlink() or not manifest.resolve().is_relative_to(root) or manifest.stat().st_size>1024*1024:limited=True;continue
            data=json.loads(manifest.read_text(encoding='utf-8'))
            if code not in data.get('tasks',[]) and not (context_group and context_group==data.get('context_group')):continue
            for name,entry in sorted(data.get('files',{}).items()):
                original=safe_path(root,name);sha=entry.get('sha256');blob=entry.get('blob','')
                if sha is None:continue
                if not isinstance(blob,str) or not blob.isdecimal() or not isinstance(sha,str) or len(sha)!=64:limited=True;continue
                saved=directory/blob
                if saved.is_symlink() or not saved.resolve().is_relative_to(directory.resolve()):limited=True;continue
                size=saved.stat().st_size
                if size>32*1024*1024 or size>budget:limited=True;continue
                budget-=size
                if hashlib.sha256(saved.read_bytes()).hexdigest()!=sha:limited=True;continue
                # Restored files may legitimately evolve. Do not inject older
                # drafts again when the live file is available.
                if pointer['state']=='restored' and original.exists():continue
                items.append({'path':name,'read_path':str(saved),'sha256':sha,'source_branch':data['branch'],'source_tasks':data['tasks'],'checkpoint_state':pointer['state']})
        except (OSError,ValueError,TypeError,KeyError,AttributeError):limited=True
    if not items and not limited:return None
    return {'items':items[after:after+limit],'total':len(items),'has_more':after+limit<len(items),'next_after':min(len(items),after+limit),'limited':limited,
            'instruction':'Preserved input references are read-only saved drafts, not proof of completed acceptance. Read these verified copies when an original input is missing. Do not restore another task branch, overwrite current files or discard its checkpoint.'}
