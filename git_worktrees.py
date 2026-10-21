"""Local, persistent Git workspaces; the canonical board and locks stay central."""
import hashlib
import json
import os
import tempfile
import subprocess
import shutil
from pathlib import Path


def identity(task):
    return task['uid'] if 'uid' in task.keys() and task['uid'] else str(task['id'])


def registry(root):
    return Path(root)/'.scrum/local/git-workspaces.json'


def entries(root):
    path=registry(root)
    if not path.exists():return {}
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Invalid Git workspace registry')
    return value


def workspace(root,task):
    if not root:return ''
    root=Path(root).resolve()
    item=entries(root).get(str(task))
    if not item:return str(root)
    if item.get('cleaned'):return ''
    path=Path(item['path']).resolve()
    expected=root/'.scrum/local/worktrees'/hashlib.sha256(item['branch'].encode()).hexdigest()[:20]
    if path!=expected.resolve() or not path.is_relative_to(root/'.scrum/local/worktrees') or not (path/'.git').is_file():
        raise ValueError('Managed Git workspace is missing or outside its expected folder; preserve it for recovery')
    return str(path)


def branch(root,task):
    item=entries(root).get(str(task))
    return item.get('branch') if item else None


def logical(root,path):
    """Normalize absolute worker paths into the central file-lock namespace."""
    root=Path(root).resolve();path=Path(path).resolve()
    managed=root/'.scrum/local/worktrees'
    if path.is_relative_to(managed):
        relative=path.relative_to(managed)
        if len(relative.parts)<2:raise ValueError('Reserve individual workspace files')
        candidate=managed/relative.parts[0]
        if not any(Path(workspace(root,key))==candidate for key in entries(root)):
            raise ValueError('Unknown managed workspace')
        return root.joinpath(*relative.parts[1:])
    return path


def files(root,planned):
    root=Path(root).resolve()
    canonical=root.parents[3] if root.parent.name=='worktrees' and root.parents[2].name=='.scrum' else root
    result=[]
    for name in planned:
        path=Path(name)
        if path.is_absolute():
            path=path.resolve()
            if path.is_relative_to(root):path=path.relative_to(root)
            elif path.is_relative_to(canonical):path=path.relative_to(canonical)
        result.append(str(path))
    return result


def prepare(root,settings,task):
    import git_sync as git
    root=Path(root).resolve();saved=entries(root)
    existing=saved.get(identity(task))
    target=existing['branch'] if existing else git.branch_for(settings,task)
    path=root/'.scrum/local/worktrees'/hashlib.sha256(target.encode()).hexdigest()[:20]
    if not path.resolve().is_relative_to(root/'.scrum/local/worktrees'):
        raise ValueError('Managed workspace path escapes the project')
    origin=git._git(root,'rev-parse','--symbolic-full-name',(existing.get('parent') if existing and existing.get('cleaned') else None) or settings.get('git_base_branch') or 'HEAD')
    parent=origin.stdout.strip()
    if origin.returncode or not parent.startswith('refs/heads/'):
        raise ValueError('Worktrees require a local origin branch')
    exists=git._git(root,'show-ref','--verify','--quiet','refs/heads/'+target).returncode==0
    destination='refs/heads/'+target if exists else parent
    for dep in task.get('dependencies',[]):
        dependency=git.branch_for(settings,dep)
        if dependency!=target and git._git(root,'show-ref','--verify','--quiet','refs/heads/'+dependency).returncode==0 and not git.is_ancestor(root,dependency,destination):
            raise ValueError('Dependency branch '+dependency+' is not integrated into the workspace destination')
    # A project can switch from Branches to Worktrees while unfinished work
    # still owns its original checkout. Git cannot check that branch out twice.
    # Reuse that checkout without registering it as a disposable worktree.
    current=git._git(root,'symbolic-ref','--short','HEAD')
    if not existing and not path.exists() and not current.returncode and current.stdout.strip()==target:
        return {'branch':target,'worktree':str(root),'canonical_root':str(root),'parent':git.parent_branch(root,target),'reused_canonical':True}
    if path.exists():
        current=git._git(path,'symbolic-ref','--short','HEAD')
        common=git._git(path,'rev-parse','--path-format=absolute','--git-common-dir')
        original=git._git(root,'rev-parse','--path-format=absolute','--git-common-dir')
        if current.returncode or current.stdout.strip()!=target or common.returncode or original.returncode or common.stdout.strip()!=original.stdout.strip():
            raise ValueError('Existing workspace does not match its branch/repository; manual recovery required')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        args=['worktree','add','--quiet']+([str(path),target] if exists else ['-b',target,str(path),parent])
        made=git._git(root,*args,timeout=20)
        if made.returncode:raise ValueError((made.stderr or made.stdout).strip()[-500:])
        if not exists:
            recorded=git._git(root,'config','--local','branch.'+target+'.scrum-parent',parent[len('refs/heads/'):])
            if recorded.returncode:raise ValueError('Could not record worktree origin')
    for value in saved.values():
        if value['branch']==target:value.pop('cleanup',None)
    saved[identity(task)]={'path':str(path),'branch':target}
    save(root,saved)
    return {'branch':target,'worktree':str(path),'canonical_root':str(root),'parent':git.parent_branch(root,target)}


def inspection_root(root,task):
    try:return workspace(root,task)
    except (OSError,ValueError,KeyError):return ''


def save(root,saved):
    location=registry(root);location.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=location.parent,prefix='workspace-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(saved,stream)
        os.replace(temporary,location)
    finally:Path(temporary).unlink(missing_ok=True)


def archive(root,confirm=False):
    """Detach managed checkouts, preserving every file and all branch refs."""
    import git_sync as git
    if not root:raise ValueError('Project has no registered workspace folder')
    root=Path(root).resolve()
    with git.checkout_lock(root):
        saved=entries(root);groups={}
        for key,item in saved.items():
            if item.get('cleaned'):continue
            path=Path(workspace(root,key))
            common=git._git(path,'rev-parse','--path-format=absolute','--git-common-dir')
            original=git._git(root,'rev-parse','--path-format=absolute','--git-common-dir')
            head=git._git(path,'symbolic-ref','--short','HEAD')
            if common.returncode or original.returncode or common.stdout.strip()!=original.stdout.strip() or head.returncode or head.stdout.strip()!=item['branch']:
                raise ValueError('Workspace repository/branch does not match its registry; preserved for recovery')
            groups.setdefault(str(path),{'path':str(path),'branch':item['branch'],'tasks':[]})['tasks'].append(key)
        if not confirm:return {'workspaces':list(groups.values()),'confirmed':False}
        reports=[]
        for location,item in groups.items():
            path=Path(location)
            if not path.is_relative_to(root/'.scrum/local/worktrees') or path==root:
                raise ValueError('Refusing to detach a path outside managed worktrees')
            archive_root=root/'.scrum/local/worktree-archives'
            if not archive_root.resolve().is_relative_to(root/'.scrum/local'):
                raise ValueError('Archive path escapes project-local storage')
            archive_root.mkdir(parents=True,exist_ok=True)
            archive_dir=Path(tempfile.mkdtemp(prefix='workspace-',dir=archive_root))
            contents=archive_dir/'files';contents.mkdir()
            manifest=dict(item,head=git._git(path,'rev-parse','HEAD').stdout.strip(),state='preserving')
            record=archive_dir/'manifest.json';record.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            moved=[]
            try:
                for child in list(path.iterdir()):
                    if child.name=='.git':continue
                    shutil.move(str(child),str(contents/child.name));moved.append(child.name)
                removed=git._git(root,'worktree','remove','--force',str(path),timeout=30)
                if removed.returncode:raise ValueError('Could not detach preserved worktree: '+removed.stderr[-400:])
            except BaseException:
                if (path/'.git').is_file():
                    for name in reversed(moved):
                        if not (path/name).exists():shutil.move(str(contents/name),str(path/name))
                manifest['state']='recovery_required';record.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
                raise
            manifest['state']='archived';record.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            for key in item['tasks']:saved.pop(key,None)
            save(root,saved)
            reports.append(dict(item,archive=str(contents),head=manifest['head']))
        save(root,{key:value for key,value in saved.items() if not value.get('cleaned')})
        return {'workspaces':reports,'confirmed':True,'branches_preserved':True}


def queue_cleanup(root,merge,actor):
    import git_sync as git
    with git.checkout_lock(root):
        saved=entries(root)
        matching=[v for v in saved.values() if v['branch']==merge['source'] and not v.get('cleaned')]
        if not matching:return {'reason':'retained: no managed worktree (checked-out branches are protected)'}
        for item in matching:item['cleanup']=dict(merge,actor=actor)
        save(root,saved)
    return {'pending':True,'reason':'pending worker exit; remote branch retained'}


def related_pending(root,source,tasks,settings,prefix):
    """Conservatively retain dependency/context components with unfinished work."""
    import git_sync as git
    saved=entries(root);related=set();edges={};groups={}
    for task in tasks:
        key=task['id'];edges.setdefault(key,set())
        name=saved.get(identity(task),{}).get('branch') or git.branch_for(settings,dict(task,code=f"{prefix}-{task['number']}"))
        if name==source:related.add(key)
        for dependency in json.loads(task['dependencies'] or '[]'):
            edges[key].add(dependency);edges.setdefault(dependency,set()).add(key)
        group=(task.get('context_group') or '').strip()
        if group:
            if group in groups:
                other=groups[group];edges[key].add(other);edges.setdefault(other,set()).add(key)
            else:groups[group]=key
    if not related:return True  # Unknown/deleted task: preserve for human review.
    pending=list(related)
    while pending:
        for key in edges[pending.pop()]-related:
            related.add(key);pending.append(key)
    return any(t['id'] in related and t['status'] not in ('Done','Cancelled') for t in tasks)


def cleanup(root,actor,related_work=None):
    """Only called after the supervisor reports that its CLI process exited."""
    import git_sync as git
    results=[]
    if not any(v.get('cleanup',{}).get('actor')==actor or v.get('cleanup',{}).get('worker_exited') for v in entries(root).values()):return results
    with git.checkout_lock(root):
        saved=entries(root)
        for value in saved.values():
            if value.get('cleanup',{}).get('actor')==actor:value['cleanup']['worker_exited']=True
        for source in sorted({v['branch'] for v in saved.values() if v.get('cleanup',{}).get('worker_exited')}):
            item=next(v for v in saved.values() if v['branch']==source and v.get('cleanup',{}).get('worker_exited'))
            pending=item['cleanup'];result={'branch':source,'removed':False}
            if related_work is None or related_work(source):
                results.append(dict(result,reason='related unfinished work or unknown task state'));continue
            try:
                path=workspace(root,next(k for k,v in saved.items() if v is item))
                common=git._git(path,'rev-parse','--path-format=absolute','--git-common-dir')
                original=git._git(root,'rev-parse','--path-format=absolute','--git-common-dir')
                if common.returncode or original.returncode or common.stdout.strip()!=original.stdout.strip():raise ValueError('workspace repository changed')
                tip=git._git(root,'rev-parse','--verify','refs/heads/'+source)
                if tip.returncode or tip.stdout.strip()!=pending['source_sha']:raise ValueError('source changed or missing')
                if git._git(root,'merge-base','--is-ancestor',pending['source_sha'],'refs/heads/'+pending['target']).returncode:raise ValueError('source is no longer integrated')
                parents=git._git(root,'config','--local','--get-regexp',r'^branch\..*\.scrum-parent$')
                if parents.returncode not in (0,1):raise ValueError('cannot inspect dependent branches')
                if any(line.split(' ',1)[-1]==source for line in parents.stdout.splitlines()):raise ValueError('branch is the origin of another branch')
                trees=git._git(root,'worktree','list','--porcelain')
                if trees.returncode:raise ValueError('cannot inspect worktrees')
                for record in trees.stdout.split('\n\n'):
                    if 'branch refs/heads/'+source in record.splitlines():
                        location=next(line[9:] for line in record.splitlines() if line.startswith('worktree '))
                        if Path(location).resolve()!=Path(path).resolve():raise ValueError('branch is checked out elsewhere')
                # Include ignored files: caches/build outputs may contain valuable local data.
                status=git._git(path,'status','--porcelain','--untracked-files=all','--ignored')
                if status.returncode or status.stdout.strip():raise ValueError('workspace contains local or ignored files')
                removed=git._git(root,'worktree','remove',path,timeout=20)
                if removed.returncode:raise ValueError(removed.stderr.strip()[-300:])
                deleted=git._git(root,'update-ref','-d','refs/heads/'+source,pending['source_sha'])
                for value in saved.values():
                    if value['branch']==source:
                        value.update(cleaned=True,parent=pending['target']);value.pop('cleanup',None)
                if deleted.returncode:raise ValueError('folder removed; branch changed concurrently and was retained')
                git._git(root,'config','--local','--remove-section','branch.'+source)
                result['removed']=True
            except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
                result['reason']=str(error)[:300]
                for value in saved.values():
                    if value['branch']==source and str(error)!='branch is the origin of another branch':value.pop('cleanup',None)
            results.append(result)
        save(root,saved)
    return results

