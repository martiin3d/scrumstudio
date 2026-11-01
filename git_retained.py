"""Exact-scope private retention; never deletes, discards, resets or publishes."""
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
import git_sync as git
from git_paused import checked,paths,safe_path,save


def signature(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


DEFAULT_MAX_BYTES=32*1024*1024
SUPPORTED_MAX_BYTES=(DEFAULT_MAX_BYTES,64*1024*1024)


def preserve(root,planned,task,target,*,max_bytes=DEFAULT_MAX_BYTES):
    """Caller holds checkout lock and the sole live recovery claim/file scope."""
    root=Path(root).resolve()
    if type(max_bytes) is not int or max_bytes not in SUPPORTED_MAX_BYTES:
        raise ValueError('Choose a supported preservation bound: 32 or 64 MiB')
    if not re.fullmatch(r'[A-Za-z]+-[0-9]+',task):raise ValueError('Use the current recovery task code')
    current=git.state(root)
    if not current:raise ValueError('Git checkout unavailable')
    source=current[0]
    if source in ('HEAD','') or target==source:raise ValueError('Require distinct named source and integrated destination branches')
    base=checked(root,'rev-parse','HEAD')
    if Path(checked(root,'rev-parse','--show-toplevel')).resolve()!=root:raise ValueError('Use the repository root')
    destination=git._git(root,'rev-parse','--verify','refs/heads/'+target)
    if destination.returncode or not git.is_ancestor(root,base,destination.stdout.strip()):
        raise ValueError('Destination must already include every source commit; integrate unfinished branch work first')
    tip=destination.stdout.strip()
    if git._git(root,'diff','--cached','--quiet').returncode!=0 or git._git(root,'ls-files','--unmerged').stdout.strip():
        raise ValueError('Preserve existing staging/conflicts; this helper will not change them')
    tracked,untracked=paths(root);names=tracked|untracked
    if not names or len(names)>1000 or not names.issubset(set(planned)):
        raise ValueError('Every dirty file must belong to the explicit recovery file plan (1..1000 exact paths)')
    files={name:safe_path(root,name) for name in names}
    total_bytes=sum(p.stat().st_size for p in files.values() if p.exists())
    if total_bytes>max_bytes:raise ValueError(f'Retained edits exceed the {max_bytes//(1024*1024)} MiB preservation bound')
    key=uuid.uuid4().hex
    archive=root/'.scrum/local/retained'/(task.lower()+'-'+key)
    if not archive.resolve().is_relative_to(root/'.scrum/local'):raise ValueError('Retention path escaped local storage')
    ignored=git._git(root,'check-ignore','--',str(archive.relative_to(root)/'manifest.json'),literal_paths=False)
    if ignored.returncode:raise ValueError('Local retention must be ignored by Git')
    archive.mkdir(parents=True)
    private='private-local/scrum-retained-'+task.lower()+'-'+key
    manifest={'schema':1,'state':'copying','task':task,'source_branch':source,'source_commit':base,'target':target,'target_commit':tip,'private_branch':private,'total_bytes':total_bytes,'max_bytes':max_bytes,'files':{}}
    for name,path in files.items():
        sha=signature(path);backup=archive/'files'/name
        if sha is not None:
            backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
            if signature(backup)!=sha:raise ValueError('Retention copy failed verification; originals untouched')
        manifest['files'][name]={'sha256':sha,'bytes':path.stat().st_size if sha is not None else 0,'untracked':name in untracked}
    save(archive/'manifest.json',manifest)
    def unchanged():
        if any(signature(safe_path(root,n))!=entry['sha256'] or (safe_path(root,n).stat().st_size if safe_path(root,n).exists() else 0)!=entry['bytes'] for n,entry in manifest['files'].items()):
            raise ValueError('Checkout changed during retention; inspect the saved manifest before retrying')
    unchanged()
    if sum(entry['bytes'] for entry in manifest['files'].values())!=total_bytes:
        raise ValueError('Checkout size changed during retention; originals and backups preserved')
    if paths(root)!=(tracked,untracked) or checked(root,'rev-parse','HEAD')!=base or git.state(root)[0]!=source:
        raise ValueError('Checkout scope changed during retention; originals and backups preserved')
    if git._git(root,'diff','--cached','--quiet').returncode!=0:raise ValueError('Staging changed; originals preserved')
    checked(root,'switch','-c',private)
    if tracked:
        for chunk in git.path_chunks(sorted(tracked)):checked(root,'add','--',*chunk)
        staged=set(git._git(root,'diff','--cached','--no-renames','--name-only','-z').stdout.rstrip('\0').split('\0'))-{''}
        if staged!=tracked:raise ValueError('Unexpected staged files; private branch and backup retained for reconciliation')
        unchanged()
        checked(root,'commit','-m',task+': privately retain unresolved checkout edits; not for publication')
    manifest.update(state='tracked-retained',private_commit=checked(root,'rev-parse','HEAD'));save(archive/'manifest.json',manifest)
    unchanged()
    # Move exact files, with no recursive deletion and no overwrite. Byte copies
    # plus the original file are retained, and unknown tracked edits stay private.
    for name in sorted(untracked):
        path=safe_path(root,name);dest=archive/'moved'/name
        if signature(path)!=manifest['files'][name]['sha256']:raise ValueError('Generated file changed before its move; preservation stopped')
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():raise ValueError('Refuse retention overwrite')
        path.rename(dest)
        if signature(dest)!=manifest['files'][name]['sha256']:raise ValueError('Moved file failed verification; inspect saved manifest')
    if git.state(root)!=(private,[]):
        import git_index
        git_index.refresh_identical(root)
    if git.state(root)!=(private,[]):raise ValueError('Additional edits appeared; retained branch is not clean, no destination switch')
    if checked(root,'rev-parse','refs/heads/'+target)!=tip:raise ValueError('Destination changed; preserve retained work and reconcile')
    checked(root,'switch',target)
    if git.state(root)!=(target,[]):raise ValueError('Destination is not clean; retained copies remain available')
    manifest['state']='retained';save(archive/'manifest.json',manifest)
    return {'preserved':True,'tracked_files':len(tracked),'untracked_files':len(untracked),'total_bytes':total_bytes,'max_bytes':max_bytes,'hashes_verified':True,'private_branch':private,'private_commit':manifest['private_commit'],'archive':str(archive.relative_to(root)),'manifest':str((archive/'manifest.json').relative_to(root)),'checkout':target,'clean':True,'published':False,'deleted_files':0}
