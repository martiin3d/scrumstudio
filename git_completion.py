"""Native completion receipts: verification survives failed Git integration."""
import json
import os
import secrets
import re
import shutil
import subprocess
import time
from pathlib import Path
import git_sync as git


class RecoveryIncomplete(ValueError):
    """Known pre-transition rejection; ownership and completion stay intact."""


def location(root, task):
    return Path(root)/'.scrum'/'local'/'git-completions'/(str(int(task))+'.json')


def read_receipt(root,task):
    path=location(root,task)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def write_receipt(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.'+secrets.token_hex(8)+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return data


def file_blobs(root,planned,include_ignored=False):
    result={};existing=[]
    for name in git.planned_paths(root,planned,include_ignored=include_ignored):
        if not (Path(root)/name).exists():result[name]=None;continue
        existing.append(name)
    if existing:
        # stdin avoids Windows argv limits and hundreds of process launches.
        # Git's C-quoted path input retains per-path attributes and clean filters.
        paths=''.join(json.dumps(name,ensure_ascii=False)+'\n' for name in existing)
        value=git._git(root,'hash-object','--stdin-paths',input_text=paths,timeout=30)
        hashes=value.stdout.splitlines()
        if value.returncode or len(hashes)!=len(existing) or any(not h or any(c not in '0123456789abcdef' for c in h) or len(h) not in (40,64) for h in hashes):
            raise ValueError('Could not fingerprint planned files; verified completion remains pending')
        result.update(zip(existing,hashes))
    return result


def matches_commit(root,files,sha):
    for names in git.path_chunks(list(files)):
        result=git._git(root,'ls-tree','-z',sha,'--',*names,timeout=15)
        if result.returncode:return False
        entries={}
        for item in filter(None,result.stdout.split('\0')):
            metadata,separator,name=item.partition('\t');entry=metadata.split()
            if not separator or len(entry)!=3:return False
            entries[name]=entry[2]
        if any(entries.get(name)!=files[name] for name in names):return False
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
    anchor=prior.get('target_anchor') if reuse else git._git(root,'rev-parse','--verify','refs/heads/'+target).stdout.strip()
    if anchor:data['target_anchor']=anchor
    if not pending:
        if not matches_commit(root,files,sha):raise ValueError('Commit differs from verified files; verification intent retained')
        if reuse and recovered_tip(root,data)!=sha:raise ValueError('Commit includes unverified changes; verification intent retained')
    return write_receipt(path,data)


def github_pr(root,remote,number):
    origin=git._git(root,'remote','get-url',remote)
    match=re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?',origin.stdout.strip())
    if origin.returncode or not match:raise ValueError('PR reconciliation requires a configured GitHub remote')
    gh=shutil.which('gh')
    if not gh:raise ValueError('Install and authenticate GitHub CLI to reconcile this PR')
    repository=match[1]
    fields='number,url,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,mergeCommit,statusCheckRollup'
    result=subprocess.run([gh,'pr','view',str(number),'--repo','github.com/'+repository,'--json',fields],
                          cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=25,
                          env={**os.environ,'GH_PROMPT_DISABLED':'1','GH_HOST':'github.com'})
    if result.returncode:raise ValueError('GitHub CLI could not read the configured pull request')
    data=json.loads(result.stdout)
    if data.get('number')!=number or data.get('url')!=f'https://github.com/{repository}/pull/{number}':
        raise ValueError('Pull request identity does not match the configured remote')
    return data


def pr_check_state(checks):
    if not checks:return 'unreported'
    successful={'SUCCESS','NEUTRAL','SKIPPED'}
    states=[(check.get('conclusion') or check.get('state') or check.get('status') or '').upper() for check in checks]
    if any(state in {'FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED','STALE','STARTUP_FAILURE'} for state in states):
        return 'failed'
    return 'passed' if all(state in successful for state in states) else 'pending'


def material_drift(root,receipt,target_tip):
    anchor=receipt.get('target_anchor')
    if not anchor or anchor==target_tip:return False
    if not git.is_ancestor(root,anchor,target_tip):return True
    for paths in git.path_chunks(list(receipt['files'])):
        diff=git._git(root,'diff','--quiet',anchor,target_tip,'--',*paths,timeout=15)
        if diff.returncode:return True
    return False


def reconcile_pr(db,a,project_row,prefix,activity,now,resolve_dependents):
    """Observe a GitHub PR by number and validate its landing against the sealed source."""
    import git_worktrees
    import reviews
    settings=dict(project_row)
    if settings.get('git_merge')!='PR':raise ValueError('The project must use PR delivery')
    row=db.execute("SELECT * FROM tasks WHERE id=? AND project=? AND status IN ('In Review','Done')",(a['id'],settings['id'])).fetchone()
    if not row:raise ValueError('The task must have a verified PR delivery')
    if row['lock_until']>time.time():raise ValueError('Wait for the task claim to close before reconciling')
    root=git_worktrees.workspace(settings['workspace_root'],git_worktrees.identity(row))
    receipt=read_receipt(root,row['id'])
    if not receipt or not receipt.get('sha') or receipt['basis']!=git.completion_basis(dict(row)):
        raise ValueError('Current acceptance has no matching verified source receipt')
    if row['status']=='Done':
        recorded=receipt.get('pr') or {}
        if recorded.get('number')!=a['number']:raise ValueError('Task already completed through another PR')
        return {'status':'Done','pull_request':recorded}
    remote=settings['git_remote'];pr=github_pr(root,remote,a['number'])
    if (pr.get('isCrossRepository') or pr.get('headRefName')!=receipt['source'] or
        pr.get('baseRefName')!=receipt['target'] or pr.get('headRefOid')!=receipt['sha']):
        raise ValueError('PR head, verified SHA or recorded destination changed; reverify the task')
    ci=pr_check_state(pr.get('statusCheckRollup'))
    status={'number':a['number'],'url':pr['url'],'head':receipt['sha'],'base':receipt['target'],'ci':ci,
            'state':'draft' if pr.get('isDraft') else pr.get('state','unknown').lower()}
    def remember():
        receipt['pr']=status
        write_receipt(location(root,row['id']),receipt)
        return {'status':'In Review','pull_request':status}
    if status['state']!='merged' or ci!='passed':return remember()
    landed=(pr.get('mergeCommit') or {}).get('oid','')
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',landed):raise ValueError('GitHub did not report a valid landing commit')
    with git.checkout_lock(root):
        target='refs/heads/'+receipt['target'];remote_target='refs/remotes/'+remote+'/'+receipt['target']
        fetched=git._git(root,'fetch','--quiet','--no-tags',remote,target+':'+remote_target,timeout=git.PUSH_TIMEOUT)
        if fetched.returncode or not git.is_ancestor(root,landed,remote_target):
            status['state']='landing_unconfirmed'
            return remember()
        parent=git._git(root,'rev-parse','--verify',landed+'^1')
        actual=git._git(root,'rev-parse','--verify',landed+'^{tree}')
        anchor=receipt.get('target_anchor')
        expected=git._git(root,'merge-tree','--write-tree',parent.stdout.strip(),receipt['sha'],timeout=15) if not parent.returncode else None
        if (not anchor or not expected or expected.returncode or actual.returncode or
            expected.stdout.strip()!=actual.stdout.strip() or not matches_commit(root,receipt['files'],landed)):
            status['state']='reverification_required'
            return remember()
        if material_drift(root,receipt,parent.stdout.strip()):
            status['state']='reverification_required'
            return remember()
        local=git._git(root,'rev-parse','--verify',target)
        if local.returncode or not git.is_ancestor(root,local.stdout.strip(),remote_target):
            status['state']='local_target_diverged'
            return remember()
        trees=git._git(root,'worktree','list','--porcelain')
        if trees.returncode:raise ValueError('Cannot inspect Git worktrees')
        if not git.is_ancestor(root,landed,target):
            checked_out=next((block for block in trees.stdout.split('\n\n') if 'branch '+target in block.splitlines()),None)
            if checked_out:
                path=next((line[9:] for line in checked_out.splitlines() if line.startswith('worktree ')),None)
                if not path or Path(path).resolve()!=Path(settings['workspace_root']).resolve() or git.state(path)!=(receipt['target'],[]):
                    status['state']='local_target_update_pending'
                    return remember()
                updated=git._git(path,'merge','--ff-only',remote_target,timeout=20)
            else:updated=git._git(root,'update-ref',target,remote_target,local.stdout.strip())
            if updated.returncode:
                status['state']='local_target_update_pending'
                return remember()
        status.update(state='integrated',landing=landed)
        if reviews.data(row).get('required') and reviews.data(row).get('state')!='approved':
            status['state']='human_review_pending'
            return remember()
        if settings.get('git_mode')=='Branches':
            live=db.execute('SELECT 1 FROM tasks WHERE project=? AND lock_until>?',(settings['id'],time.time())).fetchone()
            reserved=db.execute('SELECT 1 FROM file_locks WHERE project=? AND lock_until>?',(settings['id'],time.time())).fetchone()
            if live or reserved:
                status['state']='checkout_pending'
                return remember()
            checkout=git.close_verified_checkout(root,settings,receipt['source'],receipt['target'],receipt['sha'],
                                                 {'code':f"{prefix}-{row['number']}",'files':json.loads(row['files'])},landed=landed)
            if not checkout.get('closed'):
                status.update(state='checkout_pending',checkout=checkout)
                return remember()
    remember()
    db.execute("UPDATE tasks SET status='Done',updated=? WHERE id=?",(now(),row['id']))
    import telemetry
    telemetry.transition(db,row['id'],'Done',a['actor'])
    activity(db,row['id'],a['actor'],'comment','Verified PR integrated: '+status['url'])
    unblocked=resolve_dependents(db,settings['id'],row['id'],prefix,a['actor'])
    return {'status':'Done','pull_request':status,'unblocked_total':len(unblocked)}


def already_integrated(root,settings,task,branch,record=True):
    """Idempotent explicit finish; never checkout/stage another task's work."""
    parent=git.parent_branch(root,branch)
    if not parent:return None
    source=git._git(root,'rev-parse','--verify','refs/heads/'+branch)
    target=git._git(root,'rev-parse','--verify','refs/heads/'+parent)
    if source.returncode or target.returncode or not git.is_ancestor(root,source.stdout.strip(),target.stdout.strip()):return None
    files=file_blobs(root,task.get('files') or [])
    if not matches_commit(root,files,source.stdout.strip()):return None
    receipt=save_receipt(root,task,branch,expected_blobs=files) if record else {'sha':source.stdout.strip()}
    merged=git.merge_completed(root,settings,branch,expected_source=receipt['sha'],expected_parent=parent)
    return {'branch':branch,'commit':{'committed':False,'reason':'nothing to commit'},'push':{'pushed':False,'reason':'Existing task commit already integrated; publication verified on target'},'merge':merged,'verified_source_sha':receipt['sha']}


def validate_recovery(db,project_row,task,prefix):
    """Equal branch tips do not prove that a task's outputs were committed."""
    marker='Resolve Git integration: '
    if task.get('category')!='Git recovery' or not task['title'].startswith(marker):return
    source,separator,target=task['title'][len(marker):].partition(' into ')
    if not separator:raise RecoveryIncomplete('Git recovery requires its recorded source and target')
    import git_worktrees
    settings=dict(project_row)
    for original in db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery'",(project_row['id'],)).fetchall():
        original=dict(original)
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(original))
        path=location(root,original['id'])
        receipt=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        original.update(code=f"{prefix}-{original['number']}",files=json.loads(original['files']))
        if (receipt.get('source') if receipt else git.branch_for(settings,original))!=source:continue
        tip=git._git(root,'rev-parse','--verify','refs/heads/'+source)
        files=receipt['files'] if receipt else file_blobs(root,original['files'])
        # Ignored reports are retained local evidence, not publication scope.
        # Reconciliation separately checks their unchanged bytes before it can
        # complete the original task; never drop them from its receipt here.
        if receipt:
            ignored=git.ignored_paths(root,list(files))
            files={name:blob for name,blob in files.items() if name not in ignored}
        if tip.returncode or (not receipt and original['files'] and not files) or not matches_commit(root,files,tip.stdout.strip()):
            raise RecoveryIncomplete('Git recovery incomplete: verified planned files are not committed in '+source+'; preserve and publish the original outputs before completing recovery')
        if not git.is_ancestor(root,tip.stdout.strip(),'refs/heads/'+target):
            raise RecoveryIncomplete('Git recovery incomplete: target does not contain the verified source')
        if settings.get('git_push')!='Never':
            published=git._git(root,'ls-remote','--heads',settings.get('git_remote','origin'),'refs/heads/'+target,timeout=git.PUSH_TIMEOUT)
            remote_sha=published.stdout.split()[0] if not published.returncode and published.stdout.strip() else ''
            if not remote_sha or not git.is_ancestor(root,tip.stdout.strip(),remote_sha):
                raise RecoveryIncomplete('Git recovery incomplete: remote target publication is not verified')


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
    if settings.get('git_mode')=='Branches':
        receipt['checkout_pending']=True
        write_receipt(receipt_path,receipt)
        # The DB transaction excludes new claims through the checkout boundary;
        # existing workers (including humans) always retain their workspace.
        live=db.execute('SELECT 1 FROM tasks WHERE project=? AND lock_until>? LIMIT 1',(project_row['id'],time.time())).fetchone()
        reserved=db.execute('SELECT 1 FROM file_locks WHERE project=? AND lock_until>? LIMIT 1',(project_row['id'],time.time())).fetchone()
        if live or reserved:return dict(pending,integration_note='Integration verified; another live claim still owns the checkout')
        try:
            with git.checkout_lock(root):
                checkout=git.close_verified_checkout(root,settings,receipt['source'],receipt['target'],receipt['sha'],{'code':f"{prefix}-{row['number']}",'files':json.loads(row['files'])})
        except (OSError,ValueError,git.subprocess.SubprocessError) as error:
            checkout={'closed':False,'reason':str(error)[:300]}
        outcome['checkout']=checkout
        if not checkout.get('closed'):return dict(pending,checkout=checkout,integration_note='Integration verified; checkout closure pending: '+checkout.get('reason','unknown'))
        receipt.pop('checkout_pending',None)
        write_receipt(receipt_path,receipt)
        if checkout.get('retained'):
            kept=checkout['retained']
            activity(db,row['id'],a.get('actor'),'comment','Unplanned leftovers retained privately by native closure, without another model: '+kept['manifest']+'. Not merged or published.')
    import reviews
    review=reviews.data(row)
    if review.get('required') and review.get('state')!='approved':
        return dict(pending,awaiting_human_review=True)
    # The receipt identifies immutable verified content; later source commits do
    # not silently enlarge the outcome. No Done event precedes integration.
    db.execute("UPDATE tasks SET status='Done',updated=? WHERE id=?",(now(),row['id']))
    import telemetry
    telemetry.transition(db,row['id'],'Done',a.get('actor'))
    activity(db,row['id'],a.get('actor'),'comment','Verified outcome integrated into '+receipt['target']+'; task completed.')
    unblocked=resolve_dependents(db,project_row['id'],row['id'],prefix,a.get('actor'))
    return {'status':'Done','integration_pending':False,'unblocked':unblocked[:20],'unblocked_total':len(unblocked),**({'checkout':checkout} if settings.get('git_mode')=='Branches' else {})}


def reconcile(db,a,project_row,prefix,activity,now,resolve_dependents):
    """Called after an actual recovery completes, never by a polling model."""
    if a.get('capability') is not git.FINALIZE_TOKEN:raise ValueError('Native Git reconciliation only')
    if project_row['git_merge']!='Task':return {'completed':[]}
    import git_worktrees
    completed=[]
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery'",(project_row['id'],)).fetchall():
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(row))
        path=location(root,row['id'])
        if not path.exists():continue
        receipt=json.loads(path.read_text(encoding='utf-8'))
        if receipt['source']!=a.get('source') or receipt['basis']!=git.completion_basis(dict(row)):continue
        target='refs/heads/'+receipt['target']
        if not receipt.get('sha') and receipt.get('files'):
            excluded=git.ignored_paths(root,list(receipt['files']))
            if excluded:
                # Migrate only unchanged legacy local evidence. Missing/changed
                # reports cannot be dropped to make an old verification pass.
                local={name:receipt['files'][name] for name in excluded}
                if file_blobs(root,list(excluded),include_ignored=True)!=local:continue
                receipt={**receipt,'files':{name:blob for name,blob in receipt['files'].items() if name not in excluded},'local_evidence':{**receipt.get('local_evidence',{}),**local}}
        sha=receipt.get('sha') or recovered_tip(root,receipt)
        if not sha or not git.is_ancestor(root,sha,target):continue
        if not receipt.get('sha'):
            receipt['sha']=sha;write_receipt(path,receipt)
        merge={'merged':True,'source_sha':sha,'target':receipt['target']}
        if project_row['git_push']!='Never' and a.get('only_published'):
            remote='refs/remotes/'+project_row['git_remote']+'/'+receipt['target']
            known=git._git(root,'rev-parse','--verify',remote)
            if known.returncode or not git.is_ancestor(root,sha,remote):continue
            merge['pushed']=True
        elif project_row['git_push']!='Never':
            # A scoped recovery may have published manually; normal push is
            # idempotent and verifies publication without any inference turn.
            published=git._git(root,'push','--quiet',project_row['git_remote'],target+':'+target,timeout=git.PUSH_TIMEOUT)
            if published.returncode:continue
            merge['pushed']=True
        result=finalize(db,dict(a,id=row['id'],root=root,expected=receipt['basis'],outcome={'merge':merge}),project_row,prefix,activity,now,resolve_dependents)
        if result['status']=='Done':completed.append(row['id'])
    return {'completed':completed}


def reconcile_finished(db,project_row,prefix,activity,now,resolve_dependents,actor):
    """Existing launch boundary only; no network, polling or model call."""
    if not db.execute("SELECT 1 FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery' LIMIT 1",(project_row['id'],)).fetchone():return []
    rows=db.execute("SELECT title FROM tasks WHERE project=? AND status='Done' AND category='Git recovery' AND title LIKE 'Resolve Git integration: %'",(project_row['id'],)).fetchall()
    sources={r['title'][len('Resolve Git integration: '):].split(' into ',1)[0] for r in rows}
    import git_worktrees
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery'",(project_row['id'],)):
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(row))
        path=location(root,row['id'])
        if path.exists():
            receipt=json.loads(path.read_text())
            if receipt.get('checkout_pending') and receipt.get('basis')==git.completion_basis(dict(row)):
                sources.add(receipt['source'])
    completed=[]
    for source in sources:
        result=reconcile(db,dict(project=project_row['id'],actor=actor,source=source,capability=git.FINALIZE_TOKEN,only_published=True),project_row,prefix,activity,now,resolve_dependents)
        completed.extend(result['completed'])
    return completed
