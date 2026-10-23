"""Native completion receipts: verification survives failed Git integration."""
import json
import os
import secrets
from pathlib import Path
import git_sync as git


def location(root, task):
    return Path(root)/'.scrum'/'local'/'git-completions'/(str(int(task))+'.json')


def write_receipt(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.'+secrets.token_hex(8)+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return data


def file_blobs(root,planned):
    result={}
    for name in git.planned_paths(root,planned):
        if not (Path(root)/name).exists():result[name]=None;continue
        value=git._git(root,'hash-object','--path='+name,'--',name)
        if value.returncode:raise ValueError('Could not fingerprint planned file '+name)
        result[name]=value.stdout.strip()
    return result


def matches_commit(root,files,sha):
    for name,expected in files.items():
        result=git._git(root,'ls-tree','-z',sha,'--',name)
        if result.returncode:return False
        entry=result.stdout.split('\t',1)[0].split()
        actual=entry[2] if len(entry)==3 else None
        if actual!=expected:return False
    return True


def recovered_tip(root,receipt):
    tip=git._git(root,'rev-parse','--verify','refs/heads/'+receipt['source'])
    if tip.returncode:return None
    sha=tip.stdout.strip();files=receipt.get('files')
    if files is None or not matches_commit(root,files,sha):return None
    base=receipt.get('base')
    if not base or not git.is_ancestor(root,base,sha):return None
    changed=git._git(root,'diff','--name-only','-z',base,sha)
    if changed.returncode or not set(filter(None,changed.stdout.split('\0'))).issubset(files):return None
    return sha


def save_receipt(root, task, branch, pending=False, expected_blobs=None):
    target=git.parent_branch(root,branch)
    tip=git._git(root,'rev-parse','--verify','refs/heads/'+branch)
    if tip.returncode or not target:raise ValueError('Cannot record the verified source and its parent')
    sha=tip.stdout.strip();path=location(root,task['id'])
    prior=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    reuse=not pending and expected_blobs is None and prior.get('sha') is None and prior.get('basis')==task['completion_basis'] and 'files' in prior
    files=prior['files'] if reuse else file_blobs(root,task.get('files') or []) if expected_blobs is None else expected_blobs
    data={'basis':task['completion_basis'],'source':branch,'target':target,'sha':None if pending else sha,'files':files,'base':prior['base'] if reuse else sha}
    if not pending:
        if not matches_commit(root,files,sha):raise ValueError('Commit differs from verified files; verification intent retained')
        if reuse and recovered_tip(root,data)!=sha:raise ValueError('Commit includes unverified changes; verification intent retained')
    return write_receipt(path,data)


def already_integrated(root,settings,task,branch):
    """Idempotent explicit finish; never checkout/stage another task's work."""
    parent=git.parent_branch(root,branch)
    if not parent:return None
    source=git._git(root,'rev-parse','--verify','refs/heads/'+branch)
    target=git._git(root,'rev-parse','--verify','refs/heads/'+parent)
    if source.returncode or target.returncode or not git.is_ancestor(root,source.stdout.strip(),target.stdout.strip()):return None
    files=file_blobs(root,task.get('files') or [])
    if not matches_commit(root,files,source.stdout.strip()):return None
    receipt=save_receipt(root,task,branch,expected_blobs=files)
    merged=git.merge_completed(root,settings,branch,expected_source=receipt['sha'],expected_parent=parent)
    return {'branch':branch,'commit':{'committed':False,'reason':'nothing to commit'},'push':{'pushed':False,'reason':'Existing task commit already integrated; publication verified on target'},'merge':merged,'verified_source_sha':receipt['sha']}


def finalize(db,a,project_row,prefix,activity,now,resolve_dependents):
    if a.get('capability') is not git.FINALIZE_TOKEN:raise ValueError('Native Git completion only')
    if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT * FROM tasks WHERE project=? AND id=?',(project_row['id'],a['id'])).fetchone()
    if not row:raise ValueError('Task not found')
    pending={'status':row['status'],'integration_pending':row['status']=='In Review'}
    if row['status']!='In Review' or git.completion_basis(dict(row))!=a.get('expected'):
        return dict(pending,integration_note='Task changed after verification; saved Git result requires review')
    outcome=a.get('outcome') or {};merge=outcome.get('merge',{})
    if not merge.get('merged') or merge.get('error') or outcome.get('error'):return pending
    root=a['root'];receipt_path=location(root,row['id'])
    if not receipt_path.exists():return pending
    receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    if receipt['basis']!=a['expected'] or receipt['sha']!=merge.get('source_sha') or receipt['target']!=merge.get('target'):return pending
    if git.parent_branch(root,receipt['source'])!=receipt['target'] or not git.is_ancestor(root,receipt['sha'],'refs/heads/'+receipt['target']):return pending
    settings=dict(project_row)
    if settings.get('require_evidence') and not row['evidence'].strip():return pending
    if settings.get('git_push')!='Never' and not merge.get('pushed'):return pending
    # The receipt identifies immutable verified content; later source commits do
    # not silently enlarge the outcome. No Done event precedes integration.
    db.execute("UPDATE tasks SET status='Done',updated=? WHERE id=?",(now(),row['id']))
    import telemetry
    telemetry.transition(db,row['id'],'Done',a.get('actor'))
    activity(db,row['id'],a.get('actor'),'comment','Verified outcome integrated into '+receipt['target']+'; task completed.')
    unblocked=resolve_dependents(db,project_row['id'],row['id'],prefix,a.get('actor'))
    return {'status':'Done','integration_pending':False,'unblocked':unblocked[:20],'unblocked_total':len(unblocked)}


def reconcile(db,a,project_row,prefix,activity,now,resolve_dependents):
    """Called after an actual recovery completes, never by a polling model."""
    if a.get('capability') is not git.FINALIZE_TOKEN:raise ValueError('Native Git reconciliation only')
    if not git.integrated_completion(dict(project_row)):return {'completed':[]}
    import git_worktrees
    completed=[]
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery'",(project_row['id'],)).fetchall():
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(row))
        path=location(root,row['id'])
        if not path.exists():continue
        receipt=json.loads(path.read_text(encoding='utf-8'))
        if receipt['source']!=a.get('source') or receipt['basis']!=git.completion_basis(dict(row)):continue
        target='refs/heads/'+receipt['target']
        sha=receipt.get('sha') or recovered_tip(root,receipt)
        if not sha or not git.is_ancestor(root,sha,target):continue
        if not receipt.get('sha'):
            receipt['sha']=sha;write_receipt(path,receipt)
        merge={'merged':True,'source_sha':sha,'target':receipt['target']}
        if project_row['git_push']!='Never':
            # A scoped recovery may have published manually; normal push is
            # idempotent and verifies publication without any inference turn.
            published=git._git(root,'push','--quiet',project_row['git_remote'],target+':'+target,timeout=git.PUSH_TIMEOUT)
            if published.returncode:continue
            merge['pushed']=True
        result=finalize(db,dict(a,id=row['id'],root=root,expected=receipt['basis'],outcome={'merge':merge}),project_row,prefix,activity,now,resolve_dependents)
        if result['status']=='Done':completed.append(row['id'])
    return {'completed':completed}
