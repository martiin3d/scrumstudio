"""Materialize legacy directory placeholders at an authorized idle launch boundary."""
import json
from pathlib import Path
import time
import subprocess
import coordination as coord
import git_sync as git


def expanded(root, files):
    base=Path(root).resolve();files=coord.file_plan(files);paths=[]
    for value in files:
        raw=Path(value);path=raw if raw.is_absolute() else base/raw
        if path.is_symlink() or any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [path,*path.parents]):
            raise ValueError('Directory repair refuses linked paths')
        path=path.resolve()
        if not path.is_relative_to(base) or path==base or any(p in ('.git','.scrum') for p in path.relative_to(base).parts):
            raise ValueError('Directory repair requires scoped paths inside the checkout')
        paths.append(path)
    directories=[p for p in paths if p.is_dir()]
    if not directories:return files
    result=[p.relative_to(base).as_posix() for p in paths if not p.is_dir()]
    explicit=[p for p in paths if not p.is_dir()]
    for directory in directories:
        # Existing explicit children define the scope; a heading never broadens it.
        if any(p.is_relative_to(directory) for p in explicit):continue
        query=git._git(base,'ls-files','--cached','--others','--exclude-standard','-z','--',directory.relative_to(base).as_posix(),timeout=20)
        if query.returncode:raise ValueError('Cannot enumerate declared directory inputs')
        leaves=list(dict.fromkeys(filter(None,query.stdout.split('\0'))))
        if not leaves:raise ValueError('Declared directory has no Git-visible leaf files')
        if len(result)+len(leaves)>coord.MAX_PLANNED_FILES:raise ValueError('Directory repair exceeds file-plan limit')
        for name in leaves:
            path=base/name
            if path.is_symlink() or not path.resolve().is_relative_to(directory):raise ValueError('Unsafe directory leaf')
            result.append(name)
    result=coord.file_plan(result);coord.canonical_paths(base,result)
    return result


def at_launch(db,project,actor,activity,now):
    settings=coord.settings(project)
    if not settings.get('agent_followup_tasks') or settings.get('git_mode')!='Branches' or not project['workspace_root']:return []
    if db.execute("SELECT 1 FROM tasks WHERE project=? AND status='In Progress' AND lock_until>? LIMIT 1",(project['id'],time.time())).fetchone():return []
    if db.execute('SELECT 1 FROM file_locks WHERE project=? AND lock_until>? LIMIT 1',(project['id'],time.time())).fetchone():return []
    rows=db.execute("SELECT id,files FROM tasks WHERE project=? AND owner_id=? AND status IN ('TODO','Blocked','In Progress') AND lock_until<=?",(project['id'],actor,time.time())).fetchall()
    repaired=[]
    for row in rows:
        before=json.loads(row['files'])
        if not any((Path(project['workspace_root'])/p).is_dir() for p in before):continue
        try:
            with git.checkout_lock(project['workspace_root']):after=expanded(project['workspace_root'],before)
        except (ValueError,OSError,subprocess.SubprocessError):continue
        if before==after:continue
        if coord.conflicts(db,coord.canonical_paths(project['workspace_root'],after),row['id'],actor):continue
        db.execute('UPDATE tasks SET files=?,updated=? WHERE id=?',(json.dumps(after),now(),row['id']))
        activity(db,row['id'],actor,'comment','Native file-plan repair: directory placeholders replaced by '+str(len(after))+' explicit Git-visible leaf paths; ignored files excluded. No files edited, committed or completed.')
        repaired.append(row['id'])
    return repaired
