"""Automatic git branch/commit/push per task or context group, run by the server.

Policy lives in project settings (git_mode, git_commit, git_push, git_base_branch,
git_branch_prefix, git_remote). Everything runs in local code at claim and Done
time, never through a model turn. Checkout preparation failures refuse the claim. Commit/push jobs run after the
database transaction, scoped to explicit files; legacy errors are reported while Done
stands. Task merge saves verification in In Review and finalizes Done only after integration. No Git operation makes an inference call.
"""
import json
import os
import re
import subprocess
import sqlite3
import time
import unicodedata
import threading
from contextlib import contextmanager
from pathlib import Path

GIT_DEFAULTS = {'git_pause_blocked':False, 'git_cleanup':False, 'git_merge':'Off', 'git_mode': 'Off', 'git_commit': 'Task', 'git_push': 'Group', 'git_base_branch': '', 'git_branch_prefix': 'scrum/', 'git_remote': 'origin'}
_jobs = threading.local()
PUSH_TIMEOUT = 60   # network; a first push of a large branch is slow, and GIT_TERMINAL_PROMPT=0 keeps it from hanging
MERGE_TREE_MIN = (2, 38)  # `git merge-tree --write-tree`
FINALIZE_TOKEN = object()  # Private in-process capability, never exposed over MCP/HTTP.

def completion_basis(row):
    import hashlib
    # Publication notes/evidence prose do not change the verified implementation.
    # Bind acceptance, scope and checklist; receipts additionally bind file blobs.
    fields=('title','description','checklist','acceptance_criteria','context_group','dependencies','owner_id')
    data={k:row.get(k) for k in fields}
    planned=row.get('files') or []
    if isinstance(planned,str):planned=json.loads(planned)
    names={Path(name).as_posix().rstrip('/') for name in planned}
    data['files']=sorted(name for name in names if not any(other.startswith(name+'/') for other in names if other!=name))
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def integrated_completion(settings):
    return enabled(settings) and settings.get('git_merge') == 'Task'

def git_version(root='.'):
    try:
        out = _git(root, '--version').stdout.strip()  # 'git version 2.44.0.windows.1'
        parts = out.split()[2].split('.')
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError, OSError, subprocess.SubprocessError):
        return None

@contextmanager
def transaction_jobs():
    previous=getattr(_jobs,'current',None)
    jobs=[];_jobs.current=jobs
    try:
        yield jobs
    except BaseException:
        jobs.clear()
        raise
    finally:
        _jobs.current=previous

@contextmanager
def checkout_lock(root):
    # Shared by all processes and folder boards using this same worktree.
    location=_git(root,'rev-parse','--git-common-dir')
    if location.returncode:raise ValueError('Cannot locate Git worktree metadata')
    path=Path(location.stdout.strip())/'scrum-automation.lock'
    if not path.is_absolute():path=Path(root)/path
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('Git automation is already running for this checkout; retry later') from None
    try:
        with os.fdopen(fd,'w') as stream:stream.write(str(os.getpid()))
        yield
    finally:path.unlink(missing_ok=True)


def validate(changed):
    if 'git_pause_blocked' in changed and (type(changed['git_pause_blocked']) not in (bool,int) or changed['git_pause_blocked'] not in (0,1)):raise ValueError('git_pause_blocked must be boolean')
    if 'git_cleanup' in changed and (type(changed['git_cleanup']) not in (bool,int) or changed['git_cleanup'] not in (0,1)):raise ValueError('git_cleanup must be boolean')
    if 'git_merge' in changed and changed['git_merge'] not in ('Off','Group','Task'):raise ValueError('git_merge must be Off, Group or Task')
    if 'git_mode' in changed and changed['git_mode'] not in ('Off', 'Branches','Worktrees'):
        raise ValueError('git_mode must be Off, Branches or Worktrees')
    if 'git_commit' in changed and changed['git_commit'] not in ('Task', 'Group'):
        raise ValueError('git_commit must be Task or Group')
    if 'git_push' in changed and changed['git_push'] not in ('Group', 'Task', 'Never'):
        raise ValueError('git_push must be Group, Task or Never')
    for key, limit in (('git_base_branch', 200), ('git_branch_prefix', 60), ('git_remote', 80)):
        value = changed.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > limit or any(c.isspace() for c in value) or '..' in value or value.startswith('-'):
            raise ValueError(key + ' must be a single git-safe token without spaces')
    if changed.get('git_remote') == '':
        raise ValueError('git_remote cannot be empty')

def _git(root, *args, timeout=3):
    flags = 0x08000000 if os.name == 'nt' else 0
    # A caller launched inside another worktree can export a foreign index or
    # object store. Keep normal identity/credential config, but scope paths to -C.
    locations={'GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES','GIT_PREFIX','GIT_CEILING_DIRECTORIES','GIT_NAMESPACE','GIT_SHALLOW_FILE','GIT_QUARANTINE_PATH'}
    env={k:v for k,v in os.environ.items() if k.upper() not in locations}
    env.update(GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never',GIT_OPTIONAL_LOCKS='0')
    return subprocess.run(['git', '--literal-pathspecs', '-C', str(root), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=flags,env=env)


def is_ancestor(root,source,target):
    result=_git(root,'merge-base','--is-ancestor',source,target)
    if result.returncode not in (0,1):
        raise ValueError('Cannot verify Git ancestry: '+(result.stderr or result.stdout).strip()[-300:])
    return result.returncode==0

def slug(text, limit=48):
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text[:limit].rstrip('-') or 'task'

def branch_for(settings, task):
    """scrum/<group> for a context group, scrum/<CODE>-<title> otherwise."""
    if task.get('git_branch'):return task['git_branch']
    prefix = settings.get('git_branch_prefix', 'scrum/')
    if settings.get('git_merge')=='Task':return prefix+task['code'].lower()
    group = '' if settings.get('git_merge')=='Task' else (task.get('context_group') or '').strip()
    return prefix + (slug(group) if group else task['code'].lower() + '-' + slug(task['title'], 40))

def task_migration_preserved(root,settings,source,tasks):
    """Accept an explicit, exact task-branch copy; never guess a WIP owner."""
    source_tip=_git(root,'rev-parse','--verify','refs/heads/'+source)
    if source_tip.returncode:return False
    matches=[]
    for task in tasks:
        if branch_for(settings,task)!=source:continue
        target=branch_for(dict(settings,git_merge='Task'),task)
        if target==source:continue
        origin=_git(root,'config','--local','--get','branch.'+target+'.scrum-migrated-from')
        if origin.returncode or origin.stdout.strip()!=source:continue
        tip=_git(root,'rev-parse','--verify','refs/heads/'+target)
        if not tip.returncode and tip.stdout.strip()==source_tip.stdout.strip() and parent_branch(root,target)==parent_branch(root,source):matches.append(target)
    return len(matches)==1


def enabled(settings):
    return settings.get('git_mode', 'Off') in ('Branches','Worktrees')

def state(root):
    """(branch, dirty paths outside .scrum) or None when git is unusable."""
    try:
        head = _git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
        if head.returncode:
            return None
        status = _git(root, 'status', '--porcelain', '--untracked-files=all', '--', '.', ':!.scrum')
        if status.returncode:
            return None
        dirty = [line[3:].strip().strip('"') for line in status.stdout.splitlines() if not line[3:].strip().strip('"').startswith('.scrum/')]
        return head.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return None

def on_claim(root, settings, task):
    if not enabled(settings) or not root:return None
    try:
        with checkout_lock(root):
            if settings.get('git_mode')=='Worktrees':
                import git_worktrees
                return git_worktrees.prepare(root,settings,task)
            return _on_claim(root,settings,task)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        return {'branch':branch_for(settings,task),'error':str(error)[:300]}

def refresh_parent(root,settings,task,target,missing,workspace=None):
    """Refresh an idle branch only to the exact tree already accepted by its parent."""
    if not task.get('allow_parent_refresh') or settings.get('git_merge') not in ('Group','Task'):return None
    parent=parent_branch(root,target)
    if not parent or parent==target:return None
    before=_git(root,'rev-parse','--verify','refs/heads/'+target)
    incoming=_git(root,'rev-parse','--verify','refs/heads/'+parent)
    if before.returncode or incoming.returncode:return None
    old=before.stdout.strip();tip=incoming.stdout.strip()
    if not all(is_ancestor(root,dep,tip) for dep in missing):return None
    trees=_git(root,'worktree','list','--porcelain')
    if trees.returncode:raise ValueError('Cannot inspect stale branch worktrees')
    checked=None
    for record in trees.stdout.split('\n\n'):
        if 'branch refs/heads/'+target in record.splitlines():
            checked=next((line[9:] for line in record.splitlines() if line.startswith('worktree ')),None)
    if checked:
        allowed={Path(root).resolve()}
        if workspace:allowed.add(Path(workspace).resolve())
        current=state(checked)
        if Path(checked).resolve() not in allowed or not current or current!=(target,[]):return None
    if not is_ancestor(root,old,tip):
        if (git_version(root) or (0,0))<MERGE_TREE_MIN:return None
        tree=_git(root,'merge-tree','--write-tree',old,tip,timeout=10)
        accepted=_git(root,'rev-parse',tip+'^{tree}')
        if tree.returncode or accepted.returncode:
            if settings.get('git_merge')=='Task':raise ValueError('Task branch conflicts with its recorded parent; preserve checkpoint and resolve the scoped conflict')
            return None
        merged_tree=tree.stdout.splitlines()[0]
        if settings.get('git_merge')!='Task' and merged_tree!=accepted.stdout.strip():return None
        merged=_git(root,'commit-tree',merged_tree,'-p',old,'-p',tip,'-m','Synchronize '+target+' from recorded parent '+parent,timeout=10)
        if merged.returncode:raise ValueError('Could not record verified parent synchronization')
        tip=merged.stdout.strip()
    if checked:
        if _git(root,'rev-parse','refs/heads/'+target).stdout.strip()!=old:raise ValueError('Stale branch changed concurrently')
        updated=_git(checked,'merge','--ff-only','--no-edit',tip,timeout=20)
    else:updated=_git(root,'update-ref','refs/heads/'+target,tip,old)
    if updated.returncode:raise ValueError('Could not fast-forward stale branch; preserve current work for review')
    return parent


def refresh_index_state(root):
    """Refresh stat metadata once; never stage content or alter an existing index delta."""
    current=state(root)
    if current and current[1] and _git(root,'diff','--cached','--quiet').returncode==0:
        with checkout_lock(root):
            if _git(root,'diff','--cached','--quiet').returncode==0:
                result=_git(root,'update-index','--refresh',timeout=5)
                if result.returncode in (0,1):return state(root)
    return current


def _on_claim(root, settings, task):
    """Move the checkout to the task's branch. Returns {'branch','switched'|'created'} or {'busy':...}/{'error':...}."""
    if not enabled(settings) or not root:
        return None
    target = branch_for(settings, task)
    current = state(root)
    if current is None:
        return {'branch': target, 'error': 'git unavailable or not a repository'}
    branch, dirty = current
    destination=target if _git(root,'rev-parse','--verify','--quiet','refs/heads/'+target).returncode==0 else settings.get('git_base_branch') or 'HEAD'
    missing=[]
    for dep in task.get('dependencies',[]):
        dependency=branch_for(settings,dep)
        if dependency==target:continue
        if _git(root,'rev-parse','--verify','--quiet','refs/heads/'+dependency).returncode==0 and not is_ancestor(root,dependency,destination):
            missing.append(dependency)
    # Refresh a resumed task even when it has no explicit dependencies. Parked
    # edits are restored afterwards; a genuine conflict remains recoverable.
    refreshed=refresh_parent(root,settings,task,target,missing) if missing or settings.get('git_merge')=='Task' else None
    if missing and not refreshed:
        return {'branch':target,'error':'Dependency branch '+missing[0]+' is not integrated into '+destination+'; integrate it before claiming'}
    extra={'refreshed_from':refreshed} if refreshed else {}
    if branch == target:
        import git_paused
        restored=git_paused.restore(root,target,task.get('code'),task.get('allow_checkpoint_restore',True),context_group=task.get('context_group','').strip())
        return {'branch': target,**extra,**({'restored_checkpoint':restored} if restored else {})}
    if dirty:
        return {'busy': {'current_branch': branch, 'wanted': target, 'dirty': dirty[:20], 'dirty_total': len(dirty)}}
    try:
        exists = _git(root, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + target).returncode == 0
        if exists:
            result = _git(root, 'checkout', '--quiet', target, timeout=20)
            outcome = 'switched'
        else:
            base = settings.get('git_base_branch') or 'HEAD'
            if base != 'HEAD' and _git(root, 'rev-parse', '--verify', '--quiet', base).returncode:
                return {'branch': target, 'error': f'base branch {base} not found; staying on {branch}'}
            origin=_git(root,'rev-parse','--symbolic-full-name',base)
            parent=origin.stdout.strip() if not origin.returncode else ''
            if not parent.startswith('refs/heads/'):
                return {'branch':target,'error':'Branch creation requires a local origin branch so its merge destination can be recorded'}
            result = _git(root, 'checkout', '--quiet', '-b', target, base, timeout=20)
            if not result.returncode:
                recorded=_git(root,'config','--local','branch.'+target+'.scrum-parent',parent[len('refs/heads/'):])
                if recorded.returncode:return {'branch':target,'error':'Branch created but its origin could not be recorded; automatic merge is unavailable'}
            outcome = 'created'
        if result.returncode:
            return {'branch': target, 'error': (result.stderr or result.stdout).strip()[:300] or 'checkout failed'}
        import git_paused
        restored=git_paused.restore(root,target,task.get('code'),task.get('allow_checkpoint_restore',True),context_group=task.get('context_group','').strip())
        return {'branch': target, outcome: True, 'previous': branch,**extra,**({'restored_checkpoint':restored} if restored else {})}
    except (OSError, subprocess.SubprocessError) as error:
        return {'branch': target, 'error': str(error)[:300]}

def planned_paths(root,planned):
    """Exact paths only; ignore redundant folder markers, never expand folders."""
    import git_worktrees
    root=Path(root).resolve();paths=[]
    for name in git_worktrees.files(root,planned):
        path=Path(name);path=path if path.is_absolute() else root/path
        resolved=path.resolve()
        if not resolved.is_relative_to(root) or resolved==root:
            raise ValueError('Automatic commits require explicit files inside the checkout')
        relative=resolved.relative_to(root).as_posix()
        if any(part in ('.scrum','.git') for part in Path(relative).parts):raise ValueError('Local coordination or Git metadata cannot be committed')
        paths.append((resolved,relative))
    result=[]
    for path,relative in paths:
        if path.is_dir():
            if not any(other!=path and not other.is_dir() and other.is_relative_to(path) for other,_ in paths):
                raise ValueError('Automatic commits require explicit files inside the checkout')
            continue
        if path.exists() or _git(root,'ls-files','--error-unmatch','--',relative).returncode==0:result.append(relative)
    return sorted(set(result))


def _commit(root, settings, task, message):
    if not task.get('files'):return {'committed':False,'reason':'no explicit planned files; automatic commit skipped'}
    try:pathspec=planned_paths(root,task['files'])
    except ValueError as error:return {'error':str(error)}
    if not pathspec:return {'committed':False,'reason':'no planned file exists in the checkout'}
    # Refuse pre-staged work on owned paths too: do not overwrite a human's index.
    staged=_git(root,'diff','--cached','--quiet','--',*pathspec)
    if staged.returncode==1:return {'error':'Planned files already contain staged changes; review them before automatic commit'}
    if staged.returncode!=0:return {'error':'Could not inspect the staging area'}
    changed=_git(root,'diff','--quiet','HEAD','--',*pathspec)
    untracked=_git(root,'ls-files','--others','--exclude-standard','--',*pathspec)
    if changed.returncode==0 and not untracked.stdout.strip():return {'committed':False,'reason':'nothing to commit'}
    # --only commits these paths and preserves unrelated staged changes.
    add = _git(root, 'add', '-A', '--', *pathspec, timeout=10)
    if add.returncode:return {'error':'git add: '+(add.stderr or add.stdout).strip()[:300]}
    commit = _git(root, 'commit', '--quiet', '--only', '-m', message, '--', *pathspec, timeout=10)
    if commit.returncode:return {'error':'git commit: '+(commit.stderr or commit.stdout).strip()[:300]}
    sha = _git(root, 'rev-parse', '--short', 'HEAD').stdout.strip()
    stat = _git(root, 'diff', '--shortstat', 'HEAD~1', 'HEAD').stdout.strip()
    return {'committed': True, 'sha': sha, 'summary': stat}

def on_done(db, root, settings, task, project, actor, group_done, group_members,dependency_checkpoint=False):
    """Commit at Done according to policy and queue a push when the unit is complete.

    task: dict with id, code, title, files, context_group. group_done: every task of
    the context group is Done/Cancelled (True when the task has no group).
    """
    if not enabled(settings) or not root:return None
    report={'branch':branch_for(settings,task),'queued':True}
    current=getattr(_jobs,'current',None)
    if current is None:return {'error':'Git job requires a committed coordination transaction'}
    current.append(dict(root=root,settings=dict(settings),task=dict(task),project=project,actor=actor,group_done=group_done,members=group_members,dependency_checkpoint=dependency_checkpoint,report=report))
    return report


def parent_branch(root,source):
    try:
        result=_git(root,'config','--local','--get','branch.'+source+'.scrum-parent')
        return result.stdout.strip() if not result.returncode else ''
    except (OSError,subprocess.SubprocessError):return ''


def merge_completed(root,settings,source,*,capture_before=False,expected_source=None,expected_parent=None):
    """Merge immutable commits without changing the working tree or index."""
    base=parent_branch(root,source)
    if expected_parent is not None and base!=expected_parent:return {'merged':False,'error':'Recorded origin changed concurrently; agent must re-evaluate'}
    if not base or base=='HEAD' or base==source:return {'merged':False,'error':'Recorded origin branch is missing or invalid; an agent must confirm the destination without guessing'}
    incoming=_git(root,'rev-parse','--verify','refs/heads/'+source)
    if incoming.returncode:return {'merged':False,'error':'Source or target branch is missing'}
    tip=incoming.stdout.strip()
    if expected_source is not None and tip!=expected_source:return {'merged':False,'error':'Source changed concurrently; agent must re-evaluate'}
    for attempt in range(3):
        if parent_branch(root,source)!=base:return {'merged':False,'error':'Recorded origin changed concurrently; agent must re-evaluate'}
        try:report=_merge_completed_once(root,settings,source,base,tip)
        except ValueError as error:return {'merged':False,'error':str(error)}
        retry=report.pop('retryable',False)
        if not retry or attempt==2:
            if not capture_before:report.pop('_before',None)
            return report


def _merge_completed_once(root,settings,source,base,tip):
    """Retry only a proven target-ref race, keeping the original source fixed."""
    target='refs/heads/'+base
    current=_git(root,'rev-parse','--verify',target)
    if current.returncode:return {'merged':False,'error':'Source or target branch is missing'}
    before=current.stdout.strip()
    already=is_ancestor(root,tip,before)
    trees=_git(root,'worktree','list','--porcelain')
    if trees.returncode:return {'merged':False,'error':'Cannot inspect target worktrees'}
    target_checkout=None
    for record in trees.stdout.split('\n\n'):
        if 'branch '+target in record.splitlines():
            target_checkout=next((line[9:] for line in record.splitlines() if line.startswith('worktree ')),None)
    if target_checkout and not already:
        canonical=settings.get('workspace_root','')
        current_state=state(target_checkout)
        if settings.get('git_mode')!='Worktrees' or not canonical or Path(target_checkout).resolve()!=Path(canonical).resolve() or not current_state or current_state[1]:
            return {'merged':False,'error':'Target branch is checked out or dirty in a protected worktree; agent review required'}
    if already:
        merged=before
    elif is_ancestor(root,before,tip):
        merged=tip
    else:
        version=git_version(root)
        if version and version<MERGE_TREE_MIN:
            return {'merged':False,'error':f'Automatic merge needs git {MERGE_TREE_MIN[0]}.{MERGE_TREE_MIN[1]}+ (merge-tree --write-tree); found {version[0]}.{version[1]}. Upgrade git or set git_merge=Off'}
        tree=_git(root,'merge-tree','--write-tree',before,tip,timeout=10)
        if tree.returncode:return {'merged':False,'error':'Merge conflict or merge-tree failure; agent resolution required','details':tree.stdout[-1500:]}
        made=_git(root,'commit-tree',tree.stdout.splitlines()[0],'-p',before,'-p',tip,'-m','Merge '+source+' into '+base,timeout=10)
        if made.returncode:return {'merged':False,'error':'Could not create merge commit: '+made.stderr[-300:]}
        merged=made.stdout.strip()
    if already:
        update=None  # No ref/index/worktree mutation, including on dirty targets.
    elif target_checkout:
        if _git(root,'rev-parse',target).stdout.strip()!=before:return {'merged':False,'error':'Target changed concurrently; agent must re-evaluate'}
        update=_git(target_checkout,'merge','--ff-only','--no-edit',merged,timeout=10)
    else:update=_git(root,'update-ref',target,merged,before)
    if update is not None and update.returncode:
        latest=_git(root,'rev-parse','--verify',target)
        raced=not target_checkout and latest.returncode==0 and latest.stdout.strip()!=before and is_ancestor(root,before,latest.stdout.strip())
        return {'merged':False,'retryable':raced,'error':'Target changed concurrently; agent must re-evaluate' if raced else 'Could not update target ref; agent review required'}
    report={'merged':True,'target':base,'sha':merged,'source':source,'source_sha':tip,'_before':before}
    if already:report['already_integrated']=True
    if settings.get('git_push')!='Never':
        try:
            pushed=_git(root,'push','--quiet',settings.get('git_remote','origin'),merged+':'+target,timeout=PUSH_TIMEOUT)
            if pushed.returncode:report.update(error='Merge exists locally, but target push failed: '+pushed.stderr[-300:],pushed=False)
            else:report['pushed']=True
        except (OSError,subprocess.SubprocessError) as error:report.update(error=str(error)[:300],pushed=False)
    return report

def merge_dependency_checkpoint(root,settings,source):
    """Propagate verified work only through parents with no unknown additions."""
    chain=[];seen={source};branch=source
    for _ in range(16):
        parent=parent_branch(root,branch)
        if not parent:break
        if parent in seen:return {'merged':False,'error':'Recorded parent cycle; agent review required'}
        seen.add(parent);chain.append((branch,parent));branch=parent
    else:return {'merged':False,'error':'Recorded parent chain exceeds the automatic bound'}
    report=merge_completed(root,settings,source,capture_before=True,expected_parent=chain[0][1] if chain else None)
    before=report.pop('_before',None)
    if report.get('error') or not report.get('merged'):return report
    incoming=report['source_sha'];current=report['sha']
    for branch,parent in chain[1:]:
        target=_git(root,'rev-parse','--verify','refs/heads/'+parent)
        if target.returncode:return dict(report,pending_target=parent,error='Recorded ancestor target is missing; agent review required')
        if not (is_ancestor(root,before,target.stdout.strip()) or is_ancestor(root,before,incoming)):
            return dict(report,pending_target=parent,error='Intermediate branch '+branch+' contains additional history not verified for automatic propagation')
        following=merge_completed(root,settings,branch,capture_before=True,expected_source=current,expected_parent=parent)
        before=following.pop('_before',None)
        report.setdefault('ancestor_merges',[]).append(following)
        if following.get('error') or not following.get('merged'):
            return dict(report,pending_target=parent,error='Ancestor integration: '+following.get('error','not completed'))
        incoming=following['source_sha'];current=following['sha']
    return report


def _finish(item):
    with checkout_lock(item['root']):
        report=_finish_locked(item)
    return finish_cleanup(item,report)


def _finish_locked(item):
    root=item['root'];settings=item['settings'];task=item['task']
    report={'branch':branch_for(settings,task)}
    current=state(root)
    if current is None:return dict(report,error='git unavailable or not a repository')
    strict=integrated_completion(settings)
    if strict:
        import git_completion
        existing=git_completion.already_integrated(root,settings,task,report['branch'])
        if existing:
            report.update(existing)
            return report
    if current[0]!=report['branch']:return dict(report,error='Checkout branch changed; automatic commit and push refused')
    if strict:
        # Persist verification intent before a commit hook/publication can fail.
        git_completion.save_receipt(root,task,report['branch'],pending=True)
    group=(task.get('context_group') or '').strip();unit_done=True if strict else item['group_done'] if group else True
    if item.get('dependency_checkpoint'):
        unit_done=True
        report['note']='Published a completed dependency checkpoint; remaining tasks retain their own status'
    if strict or settings.get('git_commit','Task')=='Task' or not group:
        report['commit']=_commit(root,settings,task,f"{task['code']}: {task['title']}")
    elif unit_done:
        files=sorted({p for m in item['members'] if m['status']=='Done' for p in m.get('files',[])})
        if any(not m.get('files') for m in item['members'] if m['status']=='Done'):
            report['commit']={'error':'Every completed group member needs explicit planned files'}
        else:
            report['commit']=_commit(root,settings,dict(task,files=files),group+': '+', '.join(m['code'] for m in item['members'])[:200])
    else:report['commit']={'committed':False,'reason':'group commit deferred until the context group is complete'}
    if strict and not task.get('files') and report['commit'].get('reason')=='no explicit planned files; automatic commit skipped':
        parent=parent_branch(root,report['branch'])
        if parent and state(root)==(report['branch'],[]) and is_ancestor(root,report['branch'],'refs/heads/'+parent):
            report['commit']={'committed':False,'reason':'nothing to commit'}
    commit=report['commit'];policy=settings.get('git_push','Group')
    if strict and not commit.get('error') and (commit.get('committed') or commit.get('reason')=='nothing to commit'):
        import git_completion
        receipt=git_completion.save_receipt(root,task,report['branch'])
        report['verified_source_sha']=receipt['sha']
    if commit.get('error') or (not commit.get('committed') and commit.get('reason') not in ('nothing to commit','group commit deferred until the context group is complete')):
        report['push']={'pushed':False,'reason':'Commit not verified; automatic push skipped'}
    elif policy=='Never':report['push']={'pushed':False,'reason':'push disabled'}
    elif policy=='Task' or unit_done:
        remote=settings.get('git_remote','origin')
        try:
            push_source=report['verified_source_sha']+':refs/heads/'+report['branch'] if strict else report['branch']
            result=_git(root,'push','--quiet','-u',remote,push_source,timeout=PUSH_TIMEOUT)
            report['push']={'pushed':True,'remote':remote,'branch':report['branch']} if not result.returncode else {'pushed':False,'error':(result.stderr or result.stdout).strip()[-300:] or 'push failed'}
        except (OSError,subprocess.SubprocessError) as error:
            report['push']={'pushed':False,'error':str(error)[:300]}
    else:report['push']={'pushed':False,'reason':'waiting for the rest of the context group'}
    if settings.get('git_merge') in ('Group','Task') and unit_done:
        if (not commit.get('committed') and commit.get('reason')!='nothing to commit') or (settings.get('git_push')!='Never' and not report.get('push',{}).get('pushed')):
            report['merge']={'merged':False,'error':'Commit or source publication failed; agent review required'}
        elif commit.get('committed') or commit.get('reason')=='nothing to commit':
            try:
                if strict:report['merge']=merge_completed(root,settings,report['branch'],expected_source=receipt['sha'],expected_parent=receipt['target'])
                else:report['merge']=(merge_dependency_checkpoint if item.get('dependency_checkpoint') else merge_completed)(root,settings,report['branch'])
            except (OSError,ValueError,subprocess.SubprocessError) as error:report['merge']={'merged':False,'error':str(error)[:300]}
    return report


def finish_cleanup(item,report):
    root=item['root'];settings=item['settings']
    if settings.get('git_cleanup') and report.get('merge',{}).get('merged') and not report['merge'].get('error'):
        import git_worktrees
        try:report['cleanup']=git_worktrees.queue_cleanup(settings.get('workspace_root') or root,report['merge'],item['actor'])
        except (OSError,ValueError,subprocess.SubprocessError) as error:report['cleanup']={'reason':'retained: '+str(error)[:300]}
    return report


def describe(report):
    if not report:
        return ''
    if 'error' in report and 'commit' not in report:
        return 'git: ' + report['error']
    parts = ['git: branch ' + report['branch']]
    commit = report.get('commit', {})
    if commit.get('committed'):
        parts.append(f"committed {commit['sha']} ({commit.get('summary') or 'no stat'})")
    elif 'error' in commit:
        parts.append('COMMIT FAILED: ' + commit['error'])
    else:
        parts.append(commit.get('reason', 'no commit'))
    push = report.get('push', {})
    if push.get('queued'):
        parts.append('push queued')
    elif push.get('reason'):
        parts.append('push: ' + push['reason'])
    if report.get('merge'):
        merge=report['merge'];parts.append('merged into '+merge['target'] if merge.get('merged') and not merge.get('error') else 'MERGE FAILED: '+merge.get('error','unknown'))
        if merge.get('ancestor_merges'):
            parts.append('ancestor integration: '+', '.join(m.get('target','unknown') for m in merge['ancestor_merges'] if m.get('merged') and not m.get('error')))
    if report.get('cleanup'):parts.append('cleanup: '+report['cleanup'].get('reason','pending worker exit'))
    if report.get('note'):
        parts.append(report['note'])
    return '; '.join(parts) + '.'

def flush(call,jobs,result,external_project=None):
    """Run request-local jobs after database commit; never retain failed requests."""
    pushes=[]
    for item in jobs:
        try:outcome=_finish(item)
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            outcome={'branch':branch_for(item['settings'],item['task']),'error':str(error)[:300]}
        strict=integrated_completion(item['settings'])
        if outcome.get('error') and item['settings'].get('git_merge') in ('Group','Task') and (strict or item['group_done'] or item.get('dependency_checkpoint')):
            outcome['merge']={'merged':False,'error':outcome['error']}
        item['report'].clear();item['report'].update(outcome)
        if result.get('id')==item['task']['id']:result['git']=outcome
        project=external_project if external_project is not None else item['project']
        destination=(outcome.get('merge',{}).get('pending_target') or parent_branch(item['root'],outcome['branch'])) if item['settings'].get('git_merge') in ('Group','Task') else ''
        if strict:
            try:
                finalized=call('git_finalize',dict(project=project,id=item['task']['id'],actor=item['actor'],capability=FINALIZE_TOKEN,expected=item['task'].get('completion_basis'),outcome=outcome,root=item['root']))
                if result.get('id')==item['task']['id']:result.update(finalized)
            except (OSError,ValueError,sqlite3.Error,subprocess.SubprocessError) as error:outcome['completion_error']=str(error)[:300]
        if outcome.get('merge',{}).get('error'):
            try:
                recovery=call('task_create',dict(project=project,actor=item['actor'],title='Resolve Git integration: '+outcome['branch']+' into '+(destination or '[origin not recorded]'),description='Git recovery task. Integrate existing source '+outcome['branch']+' into '+(destination or '[origin not recorded]')+' using remote '+item['settings'].get('git_remote','origin')+('. Verified source commit: '+outcome['verified_source_sha']+'; do not include later unverified source commits' if outcome.get('verified_source_sha') else '')+'. Follow recorded parents in order; earlier edges may already be integrated. Never bypass intermediate branches. Preserve all existing work and staging. Inspect current refs and file reservations, plan and reserve any files before edits, resolve only real conflicts, run affected verification, and publish without force. Do not repeat the original completed implementation. Automatic Git is disabled for this recovery task; the agent is authorized to perform the scoped merge and push. Failure: '+outcome['merge']['error'],acceptance_criteria=['Target contains the source outcome, conflicts are resolved and verification is recorded.','Publication succeeds without force, or an external blocker is recorded without retry loops.'],category='Git recovery',priority=1,scope_size='Small',scope_reason='One integration recovery for a completed outcome',model_tier='standard',model_reason='Scoped Git integration recovery'))
                outcome['recovery_task']=recovery
                import idle_signals
                idle_signals.notify('git_recovery',{'project':project})
            except Exception as error:outcome['recovery_error']=str(error)[:300]
        try:call('git_note',dict(project=project,id=item['task']['id'],actor=item['actor'],text=describe(outcome)))
        except Exception:pass
        if 'push' in outcome and ('pushed' in outcome['push'] and (outcome['push'].get('pushed') or 'error' in outcome['push'])):
            push=dict(outcome['push'],task=item['task']['id']);pushes.append(push)
            text=f"git: pushed {outcome['branch']} to {item['settings'].get('git_remote','origin')}." if push.get('pushed') else 'git: PUSH FAILED: '+push.get('error','')
            try:call('git_note',dict(project=project,id=item['task']['id'],actor=item['actor'],text=text))
            except Exception:pass
    return pushes
