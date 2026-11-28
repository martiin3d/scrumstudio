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
import git_audit


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


PUBLICATION_RETRIES = 3
PUBLICATION_RETRY_DELAY = 60
PUBLICATION_RETRY_OPTIONS = {'limit':1,'timeout':5}


def defer_publication(root,task,settings,outcome):
    for stage,key,count in (('source','push','source_publication_attempts'),
                            ('target','merge','publication_attempts')):
        result=outcome.get(key,{})
        if not result.get('publication_transient'):
            continue
        receipt=read_receipt(root,task)
        publication={'state':'pending','stage':stage,'error':result['error'],
                     'attempts':outcome.get(count,result.get(count,1)), 'retries':0,
                     'retry_limit':PUBLICATION_RETRIES,'retry_at':time.time()+PUBLICATION_RETRY_DELAY}
        receipt['publication']=publication
        receipt['publication_policy']=[settings['git_merge'],settings['git_remote'],settings['git_push']]
        write_receipt(location(root,task),receipt)
        outcome['publication']=publication
        return


RECOVERY_TITLE = 'Resolve Git integration: '


def recovery_spec(actor,title,failure):
    source,target=failure['source'],failure['target']
    if failure.get('pr'):
        action=f'Commit any remaining verified outputs on {source} and publish it for a protected pull request into {target}.'
        criterion='The verified source commit is published for the protected pull request.'
    elif failure.get('closure'):
        action=f'Return the checkout from {source} to {target} while preserving source and staging.'
        criterion='The checkout is clean on its recorded parent and the verified delivery is preserved.'
    else:
        action=f'Integrate the existing source {source} into {target}, following the recorded parents in order.'
        criterion='The target contains the verified source and required publication is verified.'
    sha=f" Verified source commit: {failure['sha']}." if failure.get('sha') else ''
    return dict(actor=actor,title=title,category='Git recovery',allow_duplicate=True,priority=1,
                scope_size='Small',scope_reason='Existing verified delivery',model_tier='standard',
                model_reason='Scoped Git delivery recovery',
                description=f"{action}{sha} Remote: {failure['remote']}. "
                            "Preserve existing files and staging. Resolve only the recorded obstruction, "
                            "verify affected changes and publish without force. "
                            f"Failure: {git_audit.error_summary(failure['error'])}.",
                acceptance_criteria=[criterion])


def retry_publication(db,project_row,prefix,activity,now,resolve_dependents,actor,register_recovery):
    """Retry at most one sealed delivery at an idle native boundary."""
    import git_worktrees
    import runtime
    settings=dict(project_row)
    project=settings['id']
    if not git.integrated_completion(settings) or runtime.control(db,project)['paused']:
        return
    rows=db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND lock_until<=? ORDER BY id",
                    (project,time.time())).fetchall()
    for row in rows:
        root=git_worktrees.workspace(settings['workspace_root'],git_worktrees.identity(row))
        receipt=read_receipt(root,row['id'])
        publication=(receipt or {}).get('publication',{})
        state=publication.get('state')
        failure=(receipt or {}).get('recovery')
        if state in ('failed','exhausted'):
            failure=publication_failure(settings,receipt)
        if failure and receipt['basis']==git.completion_basis(dict(row)):
            if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
            register_recovery(db,project,prefix,actor,row['id'],failure)
            continue
        if state!='pending' or publication['retry_at']>time.time():
            continue
        path=location(root,row['id'])
        if receipt['basis']!=git.completion_basis(dict(row)):
            continue
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
        if runtime.control(db,project)['paused'] or runtime.reason(db,project,actor):
            return
        current=db.execute('SELECT * FROM tasks WHERE id=?',(row['id'],)).fetchone()
        if current['status']!='In Review' or current['lock_until']>time.time() or git.completion_basis(dict(current))!=receipt['basis']:
            continue
        if settings['git_mode']=='Branches' and db.execute(
                'SELECT 1 FROM tasks WHERE project=? AND lock_until>? UNION ALL '
                'SELECT 1 FROM file_locks WHERE project=? AND lock_until>? LIMIT 1',
                (project,time.time(),project,time.time())).fetchone():
            return
        with git.checkout_lock(root):
            # The original finish may have completed while launch was waiting.
            if read_receipt(root,row['id'])!=receipt:
                continue
            publication['retries']+=1
            publication['retry_at']=time.time()+PUBLICATION_RETRY_DELAY
            if publication['retries']>=PUBLICATION_RETRIES:
                publication['state']='exhausted'
            write_receipt(path,receipt)
            outcome={}
            stage=publication['stage']
            verified=''
            with git_audit.capture() as actions:
                try:
                    resume_publication(root,settings,receipt,outcome)
                    result=outcome.get('merge') or outcome.get('push',{})
                    if not result.get('error') and settings['git_push']!='Never':
                        ref='refs/heads/'+(receipt['source'] if settings['git_merge']=='PR' else result.get('target',receipt['target']))
                        expected=result.get('sha',receipt['sha'])
                        observed=git._git(root,'ls-remote','--refs',settings['git_remote'],ref,timeout=PUBLICATION_RETRY_OPTIONS['timeout'])
                        if git_audit.remote_sha(observed,ref)==expected:
                            verified=f'Verified remote {ref} at {expected}.'
                        else:
                            result.update(error='Remote publication could not be verified',publication_transient=True)
                except (OSError,ValueError,subprocess.SubprocessError) as error:
                    publication.update(state='failed',error=str(error)[:300])
                else:
                    if result.get('error'):
                        publication['error']=result['error']
                        if not result.get('publication_transient'):
                            publication['state']='failed'
                    else:
                        publication.update(state='published',error='')
                        if settings['git_merge']=='PR':
                            receipt['pr']={'state':'awaiting_pr','head':receipt['sha'],'base':receipt['target']}
            write_receipt(path,receipt)
        heading=(f"Automatic Git publication retry {publication['retries']} ({stage}) · "
                 +git_audit.refs(receipt['source'],receipt['target']))
        if verified:actions.append(verified)
        git_audit.append_history(activity,db,row['id'],actor,heading,actions)
        activity(db,row['id'],actor,'comment',
                 f"Git publication {publication['state']} after retry {publication['retries']}/{PUBLICATION_RETRIES}"
                 + ('. '+git_audit.error_summary(publication['error']) if publication['error'] else '.'))
        if publication['state']=='published' and settings['git_merge']=='Task':
            finalized=finalize(db,dict(id=row['id'],actor=actor,root=root,expected=receipt['basis'],
                                       capability=git.FINALIZE_TOKEN,outcome=outcome),
                               project_row,prefix,activity,now,resolve_dependents)
            checkout=finalized.get('checkout',{})
            if checkout.get('closed') is False:
                failure=dict(source=receipt['source'],target=receipt['target'],sha=receipt['sha'],
                             remote=settings['git_remote'],closure=True,error=checkout['reason'])
                receipt=read_receipt(root,row['id'])
                receipt['recovery']=failure
                write_receipt(path,receipt)
                register_recovery(db,project,prefix,actor,row['id'],failure)
        if publication['state'] in ('failed','exhausted'):
            register_recovery(db,project,prefix,actor,row['id'],publication_failure(settings,receipt))
        return dict(publication,task=row['id'])


def publication_failure(settings,receipt):
    publication=receipt['publication']
    policy=receipt.get('publication_policy') or [settings['git_merge'],settings['git_remote']]
    return dict(source=receipt['source'],target=receipt['target'],sha=receipt['sha'],
                remote=policy[1],pr=policy[0]=='PR',
                error=f"{publication['stage']} publication {publication['state']}: {publication['error']}")


def resume_publication(root,settings,receipt,outcome):
    publication=receipt['publication']
    source,target,sha=receipt['source'],receipt['target'],receipt['sha']
    if receipt['publication_policy']!=[settings['git_merge'],settings['git_remote'],settings['git_push']]:
        raise ValueError('Project publication policy changed')
    if not sha or git.parent_branch(root,source)!=target or not matches_commit(root,receipt['files'],sha):
        raise ValueError('Verified source or its recorded destination changed')
    tip=git._git(root,'rev-parse','--verify','refs/heads/'+source)
    if tip.returncode or tip.stdout.strip()!=sha:
        raise ValueError('Verified source branch changed')
    if publication['stage']=='source':
        remote=settings['git_remote']
        result,attempts=git.publish_verified(root,remote,sha+':refs/heads/'+source,**PUBLICATION_RETRY_OPTIONS)
        publication['attempts']+=attempts
        if result.returncode:
            outcome['push']={'error':result.stderr[-300:] or 'Source push failed',
                             'publication_transient':git.transient_publication(result)}
            return
        for key,value in (('remote',remote),('merge','refs/heads/'+source)):
            git._git(root,'config','--local','branch.'+source+'.'+key,value)
    if settings['git_merge']=='Task':
        retry_settings=dict(settings,_publication_retry_options=PUBLICATION_RETRY_OPTIONS)
        merged=git.merge_completed(root,retry_settings,source,expected_source=sha,
                                   expected_parent=target,verification=receipt)
        publication['stage']='target'
        publication['attempts']+=merged.get('publication_attempts',0)
        outcome['merge']=merged


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


def validate_recovery(db,project_row,task,prefix,activity=None,actor=None):
    with git_audit.capture() as actions:
        try:
            for original, remote_result in _validate_recovery(db,project_row,task,prefix):
                if activity is None:
                    continue
                heading=f"Native verification of {prefix}-{original['number']} recovery."
                entries=[*actions, remote_result] if remote_result else list(actions)
                for task_id in (task['id'],original['id']):
                    git_audit.append_history(activity,db,task_id,actor,heading,entries)
                actions.clear()
        except RecoveryIncomplete as error:
            error.history=actions
            raise


def _validate_recovery(db,project_row,task,prefix):
    """Equal branch tips do not prove that a task's outputs were committed."""
    marker=RECOVERY_TITLE
    if task.get('category')!='Git recovery' or not task['title'].startswith(marker):return
    source,separator,target=task['title'][len(marker):].partition(' into ')
    if not separator:raise RecoveryIncomplete('Git recovery requires its recorded source and target')
    import git_worktrees
    settings=dict(project_row)
    for original in db.execute("SELECT * FROM tasks WHERE project=? AND status IN ('In Review','Done') AND category!='Git recovery'",(project_row['id'],)).fetchall():
        original=dict(original)
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(original))
        path=location(root,original['id'])
        receipt=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        original.update(code=f"{prefix}-{original['number']}",files=json.loads(original['files']))
        if (receipt.get('source') if receipt else git.branch_for(settings,original))!=source:continue
        if receipt and target not in (receipt['target'],receipt.get('recovery',{}).get('target')):continue
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
        if settings['git_merge']=='PR':
            sha=(receipt.get('sha') or recovered_tip(root,receipt)) if receipt else None
            if not sha or tip.stdout.strip()!=sha or not source_published(root,settings,source,sha):
                raise RecoveryIncomplete('Git recovery incomplete: verified PR source publication is not verified')
            yield original, f'Verified remote refs/heads/{source} at {sha}.'
            continue
        sha=receipt.get('sha') if receipt else None
        if sha and tip.stdout.strip()!=sha:
            raise RecoveryIncomplete('Git recovery incomplete: the source branch changed after verification')
        if receipt and receipt.get('recovery',{}).get('closure') and git.state(root)!=(target,[]):
            raise RecoveryIncomplete('Git recovery incomplete: return the checkout to the recorded parent and preserve remaining work')
        if not git.is_ancestor(root,tip.stdout.strip(),'refs/heads/'+target):
            raise RecoveryIncomplete('Git recovery incomplete: target does not contain the verified source')
        remote_result=''
        if settings.get('git_push')!='Never':
            ref='refs/heads/'+target
            published=git._git(root,'ls-remote','--heads',settings.get('git_remote','origin'),ref,timeout=git.PUSH_TIMEOUT)
            remote_sha=git_audit.remote_sha(published,ref)
            if not remote_sha or not git.is_ancestor(root,tip.stdout.strip(),remote_sha):
                raise RecoveryIncomplete('Git recovery incomplete: remote target publication is not verified')
            remote_result=f'Verified remote {ref} at {remote_sha}.'
        yield original, remote_result


def source_published(root,settings,source,sha):
    ref='refs/heads/'+source
    published=git._git(root,'ls-remote','--heads',settings['git_remote'],ref,timeout=git.PUSH_TIMEOUT)
    return bool(sha and git_audit.remote_sha(published,ref)==sha)


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
    if row['blocked_reason'].startswith('Git delivery ('):
        db.execute("UPDATE tasks SET blocked_reason='',blocked_reason_kind='external' WHERE id=?",(row['id'],))
    terminal=receipt.get('publication',{}).get('state') in ('failed','exhausted')
    if terminal:
        receipt['publication'].update(state='published',error='')
    if receipt.pop('recovery',None) or terminal:
        write_receipt(receipt_path,receipt)
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
    if project_row['git_merge'] not in ('Task','PR'):return {'completed':[]}
    import git_worktrees
    completed=[]
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery'",(project_row['id'],)).fetchall():
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(row))
        path=location(root,row['id'])
        if not path.exists():continue
        receipt=json.loads(path.read_text(encoding='utf-8'))
        if receipt['source']!=a.get('source') or receipt['basis']!=git.completion_basis(dict(row)):continue
        if a.get('target') and a['target'] not in (receipt['target'],receipt.get('recovery',{}).get('target')):continue
        if project_row['git_merge']=='PR':
            failed=(receipt.get('recovery') or receipt.get('publication',{}).get('state') in ('failed','exhausted')
                    or row['blocked_reason'].startswith('Git delivery ('))
            if not failed:
                continue
            sha=receipt.get('sha') or recovered_tip(root,receipt)
            if not sha:
                continue
            if a.get('only_published'):
                remote='refs/remotes/'+project_row['git_remote']+'/'+receipt['source']
                known=git._git(root,'rev-parse','--verify',remote)
                if known.returncode or known.stdout.strip()!=sha:continue
            elif not source_published(root,project_row,receipt['source'],sha):
                continue
            receipt['sha']=sha
            receipt.pop('recovery',None)
            if receipt.get('publication'):
                receipt['publication'].update(state='published',error='')
            receipt['pr']={'state':'awaiting_pr','head':sha,'base':receipt['target']}
            write_receipt(path,receipt)
            db.execute("UPDATE tasks SET blocked_reason='',blocked_reason_kind='external' WHERE id=? AND blocked_reason LIKE 'Git delivery (%)%'",(row['id'],))
            continue
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
            with git_audit.capture() as actions:
                pushed=git._git(root,'push','--quiet',project_row['git_remote'],target+':'+target,timeout=git.PUSH_TIMEOUT)
                observed=''
                if not pushed.returncode:
                    published=git._git(root,'ls-remote','--refs',project_row['git_remote'],target,timeout=git.PUSH_TIMEOUT)
                    observed=git_audit.remote_sha(published,target)
                verified=bool(observed and git.is_ancestor(root,sha,observed))
            if verified:actions.append(f'Verified remote {target} at {observed}.')
            heading='Native recovery reconciliation · '+git_audit.refs(receipt['source'],receipt['target'])
            for task_id in {row['id'],a.get('recovery_task',row['id'])}:
                git_audit.append_history(activity,db,task_id,a.get('actor'),heading,actions)
            if not verified:continue
            merge['pushed']=True
        result=finalize(db,dict(a,id=row['id'],root=root,expected=receipt['basis'],outcome={'merge':merge}),project_row,prefix,activity,now,resolve_dependents)
        if result['status']=='Done':completed.append(row['id'])
    return {'completed':completed}


def reconcile_finished(db,project_row,prefix,activity,now,resolve_dependents,actor):
    """Existing launch boundary only; no network, polling or model call."""
    if not db.execute("SELECT 1 FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery' LIMIT 1",(project_row['id'],)).fetchone():return []
    rows=db.execute("SELECT title FROM tasks WHERE project=? AND status='Done' AND category='Git recovery' AND title LIKE ?",(project_row['id'],RECOVERY_TITLE+'%')).fetchall()
    deliveries={tuple(r['title'][len(RECOVERY_TITLE):].split(' into ',1)) for r in rows}
    deliveries={delivery for delivery in deliveries if len(delivery)==2}
    import git_worktrees
    for row in db.execute("SELECT * FROM tasks WHERE project=? AND status='In Review' AND category!='Git recovery'",(project_row['id'],)):
        root=git_worktrees.workspace(project_row['workspace_root'],git_worktrees.identity(row))
        path=location(root,row['id'])
        if path.exists():
            receipt=json.loads(path.read_text())
            published=(receipt.get('publication',{}).get('state')=='published' and
                       receipt.get('publication_policy')==['Task',project_row['git_remote'],project_row['git_push']])
            if (receipt.get('checkout_pending') or published) and receipt.get('basis')==git.completion_basis(dict(row)):
                deliveries.add((receipt['source'],receipt['target']))
    completed=[]
    for source,target in deliveries:
        result=reconcile(db,dict(project=project_row['id'],actor=actor,source=source,target=target,capability=git.FINALIZE_TOKEN,only_published=True),project_row,prefix,activity,now,resolve_dependents)
        completed.extend(result['completed'])
    return completed
