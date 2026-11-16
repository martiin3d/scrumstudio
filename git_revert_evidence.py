"""Bounded, read-only evidence of exact Git reversals of verified Scrum delivery."""
import hashlib
import re
import subprocess
from pathlib import Path

import git_completion
import git_sync
import git_worktrees


OID=re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
REVERT_MESSAGE=re.compile(r'^This reverts commit ('+OID.pattern+r')\.$',re.M)
TASK_LIMIT=250
HISTORY_LIMIT=24
RESULT_LIMIT=12


def changes(root, parent, commit):
    result=git_sync._git(root,'diff-tree','--no-commit-id','--raw','--no-renames','--no-abbrev','-r','-z',parent,commit)
    if result.returncode:return {}
    tokens=result.stdout.removesuffix('\0').split('\0')
    if not 0<len(tokens)<=200 or len(tokens)%2:
        return {}
    files={}
    for header,path in zip(tokens[::2],tokens[1::2]):
        fields=header.split()
        if len(fields)!=5 or not fields[0].startswith(':') or fields[4] not in ('A','D','M','T') or not path:
            return {}
        before=(fields[0][1:],fields[2])
        after=(fields[1],fields[3])
        files[path]=(before,after)
    return files


def exact_reversal(root, delivery, revert, parent):
    ancestry=git_sync._git(root,'merge-base','--is-ancestor',delivery,parent)
    if ancestry.returncode:return {}
    history=git_sync._git(root,'rev-list','--parents','-n','1',delivery)
    commits=history.stdout.split()
    if history.returncode or len(commits)!=2:return {}
    original=changes(root,commits[1],delivery)
    reverted=changes(root,parent,revert)
    return original if original and reverted=={path:(after,before) for path,(before,after) in original.items()} else {}


def deliveries(db, project, root):
    try:worktrees=git_worktrees.entries(root)
    except (OSError,ValueError):worktrees={}
    if not (root/'.scrum/local/git-completions').is_dir() and not worktrees:
        return {},False
    rows=db.execute("SELECT id,uid,number,evidence FROM tasks WHERE project=? AND status='Done' AND evidence<>'' "
                    'ORDER BY id DESC LIMIT ?',(project,TASK_LIMIT+1)).fetchall()
    targets={}
    for task in rows[:TASK_LIMIT]:
        try:
            receipt_root=root
            path=git_completion.location(receipt_root,task['id'])
            ident=git_worktrees.identity(task)
            if not path.is_file() and ident in worktrees:
                workspace=git_worktrees.workspace(root,ident)
                if not workspace:continue
                receipt_root=Path(workspace)
                path=git_completion.location(receipt_root,task['id'])
            if path.is_symlink() or path.stat().st_size>256*1024:continue
            receipt=git_completion.read_receipt(receipt_root,task['id'])
            if not isinstance(receipt,dict):continue
            sha=receipt.get('sha','')
            target=receipt.get('target','')
            if not isinstance(sha,str) or not OID.fullmatch(sha) or not isinstance(target,str) or not target:
                continue
            pr=receipt.get('pr') or {}
            # A native PR reconciliation can record a distinct verified landing.
            landed=pr.get('landing','') if isinstance(pr,dict) and pr.get('state')=='integrated' else ''
            if isinstance(landed,str) and OID.fullmatch(landed):sha=landed
            files=receipt.get('files')
            if not isinstance(files,dict) or not files:continue
            targets.setdefault(target,{}).setdefault(sha,[]).append((task,files))
        except (OSError,ValueError,TypeError):
            continue
    return targets,len(rows)>TASK_LIMIT


def patterns(db, project, prefix, root, start=None, end=None, *, pattern_id=None):
    if not root:return {'patterns':[],'limited':False}
    root=Path(root)
    targets,limited=deliveries(db,project,root)
    found=[]
    options=[]
    if start is not None:options=['--since-as-filter=@'+str(int(start)),'--until=@'+str(int(end))]
    for branch,by_sha in list(targets.items())[:4]:
        try:
            history=git_sync._git(root,'log','--first-parent','--max-count='+str(HISTORY_LIMIT+1),
                                  '--grep=^This reverts commit ','--format=%H%x00%P%x00%ct%x00%B','-z',
                                  *options,'refs/heads/'+branch,'--')
            if history.returncode:
                limited=True
                continue
            fields=history.stdout.removesuffix('\0').split('\0')
            if len(fields)==1:continue
            limited|=len(fields)>4*HISTORY_LIMIT
            for sha,parents,stamp,body in zip(fields[::4],fields[1::4],fields[2::4],fields[3::4]):
                if not OID.fullmatch(sha) or not OID.fullmatch(parents) or not stamp.isdigit():
                    continue
                matches=REVERT_MESSAGE.findall(body)
                if len(matches)!=1 or len(tasks:=by_sha.get(matches[0],[]))!=1:
                    continue
                delivery=matches[0]
                task,verified=tasks[0]
                identity=hashlib.sha256(f'git_revert:{task["id"]}:{delivery}:{sha}'.encode()).hexdigest()[:24]
                if pattern_id and identity!=pattern_id:continue
                files=exact_reversal(root,delivery,sha,parents)
                if not files or any(path not in verified or verified[path]!=(None if after[0]=='000000' else after[1])
                                    for path,(_,after) in files.items()):
                    continue
                found.append({
                    'pattern_id':identity,'kind':'git_revert','failure_kind':'delivery_revert',
                    'observations':1,'first_at':int(stamp),'last_at':int(stamp),
                    'scope':{'workspace':'project checkout','task_id':task['id'],'task_code':f'{prefix}-{task["number"]}',
                             'fingerprint':sha,'delivery_sha':delivery,'revert_sha':sha,'branch':branch,
                             'paths':sorted(files)[:12],'paths_total':len(files)},
                    'verification_evidence':task['evidence'][:500],
                    'action':{'destination':'regression_test','text':'Review which verified behavior escaped detection and add a targeted regression check if confirmed.'},
                })
        except (OSError,ValueError,subprocess.SubprocessError):
            limited=True
    found.sort(key=lambda item:(-item['last_at'],item['pattern_id']))
    return {'patterns':found[:RESULT_LIMIT],'limited':limited or len(targets)>4 or len(found)>RESULT_LIMIT}
