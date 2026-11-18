"""Project onboarding and the versioned project wiki."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
from urllib.parse import quote

from wiki_knowledge import INDEX, WIKI, diagnose, learning_page, learning_update, proposals, source_excerpt, wiki_file, wiki_path


SOURCES = {
    'README.md': 'Project documentation', 'AGENTS.md': 'Repository instructions',
    'package.json': 'Node.js', 'pyproject.toml': 'Python', 'requirements.txt': 'Python',
    'ProjectSettings/ProjectVersion.txt': 'Unity', 'Packages/manifest.json': 'Unity packages',
    'Cargo.toml': 'Rust', 'go.mod': 'Go', 'pom.xml': 'Java', 'Gemfile': 'Ruby',
    'composer.json': 'PHP', 'pubspec.yaml': 'Flutter', 'Makefile': 'Make',
}
QUESTIONS = (
    ('goal', 'What should this project accomplish, and for whom?'),
    ('milestone', 'What is the first outcome you want to deliver?'),
    ('acceptance', 'What would demonstrate that this outcome works?'),
    ('constraints', 'Which constraints or existing decisions should the work respect?'),
)
CAPABILITY_GUIDANCE = {
    'CI':'Link the existing workflow to the verification topic and reuse it for affected tasks.',
    'Unity':'Confirm the editor version and record the Edit Mode or Play Mode check for the first outcome.',
    'Node.js':'Confirm which manifest scripts exercise the first outcome.',
    'Python':'Confirm the environment and test entry point before recording a verification command.',
}
STATE = Path('.scrum/local/knowledge-onboarding.json')
MANIFEST = WIKI/'sources.json'
MAX_FILE = 128 * 1024
SKILLS_PAGE = 'project-skills'
PROJECT_SKILL_ROOTS = ('skills','.agents/skills','.claude/skills','.codex/skills')
SPEC_LINE = re.compile(r'^Specification: (docs/agents/[^#\n]+\.md)(?:#([^@\n]+))? @ ([a-f0-9]{64})$',re.M)


def safe_file(root, name):
    relative=PurePosixPath(name)
    if (relative.is_absolute() or '..' in relative.parts or
        any(part in ('.git','.scrum','data','node_modules','Library') or part.startswith('.env') for part in relative.parts) or
        any(c in name for c in (':','\\','\x00'))):
        return None
    path=root/relative
    if any(part.is_symlink() or getattr(part,'is_junction',lambda:False)()
           for part in (path,*path.parents) if part!=root and part.is_relative_to(root)):
        return None
    try:
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size>MAX_FILE:
            return None
        return path
    except OSError:
        return None


def project_skill_manifest(root, name):
    file=PurePosixPath(name)
    if file.name!='SKILL.md' or file.parent.parent.as_posix() not in PROJECT_SKILL_ROOTS:
        return None
    path=safe_file(root,name)
    if path is None:
        return None
    from scripts.skill_lookup import metadata
    return metadata(path)


def project_skills(root):
    items=[]
    for directory in PROJECT_SKILL_ROOTS:
        for path in sorted((root/directory).glob('*/SKILL.md')):
            relative=path.relative_to(root).as_posix()
            item=project_skill_manifest(root,relative)
            if item:
                items.append({'name':item['name'],'description':item['description'][:300],'path':relative})
    return sorted(items,key=lambda item:(item['name'].casefold(),item['path']))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def source_state(path):
    try:return digest(path.read_bytes()) if path else None
    except OSError:return None


def inventory(root):
    names=dict(SOURCES)
    for pattern,label in (('*.sln','.NET'),('*.csproj','.NET'),('.github/workflows/*.yml','CI'),('.github/workflows/*.yaml','CI')):
        for path in sorted(root.glob(pattern))[:8]:
            names[path.relative_to(root).as_posix()]=label
    sources=[]
    for name,label in names.items():
        path=safe_file(root,name)
        if path:
            sources.append({'path':name,'label':label,'sha256':source_state(path)})
    dirs=[p.name for p in sorted(root.iterdir(),key=lambda p:p.name.casefold())
          if not p.name.startswith('.') and p.name not in ('node_modules','Library','Temp','obj','bin')][:24]
    commands=[]
    package=safe_file(root,'package.json')
    if package:
        try:scripts=json.loads(package.read_bytes()).get('scripts',{})
        except (ValueError,AttributeError):scripts={}
        if isinstance(scripts,dict):
            commands=[{'command':f'npm run {key}','source':'package.json'} for key in ('test','build','lint','start','dev') if key in scripts]
    return {'kind':'existing' if sources or dirs else 'empty','sources':sources,'entries':dirs,'commands':commands}


def topic_links(root, generated):
    existing=(root/WIKI)
    return sorted(set(generated)|{
        path.relative_to(root).as_posix() for path in existing.glob('*.md')
        if path.name!='index.md' and safe_file(root,path.relative_to(root).as_posix())
    })


def draft(root, name, report, answers):
    prefix=WIKI.as_posix()+'/'
    docs={}
    lines=[f'# {name}', '', '## Goal', '', answers.get('goal') or 'Open: confirm the project goal.',
           '', '## First milestone', '', answers.get('milestone') or 'Open: choose the first outcome.',
           '', '## Acceptance', '', answers.get('acceptance') or 'Open: define a reproducible check.']
    if answers.get('constraints'):lines+=['','## Constraints','',answers['constraints']]
    docs[prefix+'project.md']='\n'.join(lines)+'\n'
    lines=['# Repository map','','## Observed sources','']
    for source in report['sources']:
        name=source['path']
        lines.append(f"- [{source['label']}](../../{quote(name)}) — `{name}`")
    if report['entries']:
        lines+=['','## Top-level entries','',', '.join(f'`{entry}`' for entry in report['entries'])]
    if not report['sources']:lines+=['No supported manifests or documentation were found in the scan.']
    docs[prefix+'architecture.md']='\n'.join(lines)+'\n'
    lines=['# Running and verifying','','## Detected commands','']
    lines.extend(f"- `{entry['command']}` in [its manifest](../../{quote(entry['source'])})."
                 for entry in report['commands'])
    if not report['commands']:lines+=['Open: confirm the project command and the evidence it produces.']
    docs[prefix+'checks.md']='\n'.join(lines)+'\n'
    links=topic_links(root,docs)
    docs[INDEX.as_posix()]='# Project knowledge\n\n'+''.join(
        f"- [{PurePosixPath(name).stem.replace('-', ' ').title()}]({quote(PurePosixPath(name).name)})\n" for name in links)
    return docs


def answers_from(value):
    if not isinstance(value,dict) or set(value)-{key for key,_ in QUESTIONS}:
        raise ValueError('Supply the onboarding answers by question key')
    if any(not isinstance(text,str) or len(text)>1000 for text in value.values()):
        raise ValueError('Each onboarding answer must be at most 1000 characters')
    if len(value.get('acceptance',''))>500:
        raise ValueError('Keep the first acceptance check within 500 characters')
    return {key:text.strip() for key,text in value.items()}


def questions_for(report):
    questions=dict(QUESTIONS)
    if report.get('kind')!='existing':return questions
    sources=report.get('sources',[])
    labels=list(dict.fromkeys(item['label'] for item in sources
                              if item['label'] not in ('Project documentation','Repository instructions')))[:4]
    if labels:
        questions['milestone']='Detected '+', '.join(labels)+'. Which existing flow or new behavior should the first milestone deliver?'
    commands=[item['command'] for item in report.get('commands',[]) if item['command'] in ('npm run test','npm run build')]
    if commands:
        questions['acceptance']='The manifest defines '+', '.join(commands)+'. What result or additional check proves the first milestone?'
    if any(item['path']=='AGENTS.md' for item in sources):
        questions['constraints']='Repository instructions were found. Are there additional constraints or project decisions to record?'
    return questions


def review_plan(root, name, saved):
    answers=saved.get('answers',{})
    pages=draft(root,name,saved['inventory'],answers)
    paths={path:source_state(safe_file(root,path)) for path in pages}
    index=INDEX.as_posix()
    existing=safe_file(root,index)
    updates=[]
    if existing:
        old=existing.read_text(encoding='utf-8-sig')
        links=[path for path in pages if path!=index and paths[path] is None]
        if links:
            pages[index]=old.rstrip()+'\n\n## Project onboarding\n\n'+''.join(
                f"- [{PurePosixPath(path).stem.title()}]({quote(PurePosixPath(path).name)})\n" for path in links)
            updates.append(index)
    core={'inventory':saved['inventory'],'answers':answers,'pages':pages,'existing':paths,'updates':updates}
    return {**core,'plan_id':digest(json.dumps(core,sort_keys=True).encode()),
            'ready':all(answers.get(key) for key,_ in QUESTIONS[:3])}


def load_state(root):
    path=root/STATE
    try:return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:return {}


def source_manifest(root):
    path=safe_file(root,MANIFEST.as_posix())
    try:
        data=json.loads(path.read_bytes()) if path else {}
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):return {}


def write_manifest(root,data):
    from scrum_cli import atomic
    target=root/MANIFEST
    if target.resolve()!=target:raise ValueError('Knowledge sources cannot be stored in a linked file')
    atomic(target,data)


GENERATED_HEADINGS = {
    (WIKI/'architecture.md').as_posix(): ('Observed sources', 'Top-level entries'),
    (WIKI/'checks.md').as_posix(): ('Detected commands',),
}


def section_spans(body):
    headings=[]
    fence=None
    for marker in re.finditer(r'^(?:(#{1,6})[ \t]+([^\r\n]+)| {0,3}(`{3,}|~{3,})([^\r\n]*))',body,re.M):
        delimiter=marker[3]
        if delimiter:
            if not fence:
                fence=delimiter
            elif delimiter.startswith(fence) and not marker[4].strip():
                fence=None
        elif not fence:
            headings.append(marker)
    sections={}
    for i,heading in enumerate(headings):
        if heading[1]!='##':continue
        title=heading[2].strip()
        end=next((other.start() for other in headings[i+1:] if len(other[1])<=2),len(body))
        end=heading.start()+len(body[heading.start():end].rstrip('\r\n'))
        sections[title]=None if title in sections else (heading.start(),end)
    return sections


def section_inputs(title, report):
    if title=='Top-level entries':
        return {'entries':report['entries']}
    return {item['path']:item['sha256'] for item in report['sources']
            if title=='Observed sources' or item['path']=='package.json'}


def generated_sections(body, titles, report):
    spans=section_spans(body)
    sections={}
    for title in titles:
        if not spans.get(title):continue
        start,end=spans[title]
        sections[title]={'sha256':digest(body[start:end].replace('\r\n','\n').encode()),
                         'inputs':section_inputs(title,report)}
    return sections


def section_provenance(root, manifest):
    recorded=dict(manifest.get('generated_sections',{}))
    previous=manifest.get('inventory')
    if not previous:return recorded
    legacy=draft(root,'',previous,{})
    for page,titles in GENERATED_HEADINGS.items():
        original=legacy[page]
        if page not in recorded and manifest.get('generated',{}).get(page)==digest(original.encode()):
            recorded[page]=generated_sections(original,titles,previous)
    return recorded


def section_refresh(root, manifest, current):
    recorded=section_provenance(root,manifest)
    generated=draft(root,'',current,{})
    proposals=[]
    for page in GENERATED_HEADINGS:
        old=recorded.get(page)
        if not old:continue
        path=safe_file(root,page)
        raw=path.read_bytes() if path else b''
        try:body=raw.decode('utf-8')
        except UnicodeDecodeError:body=''
        spans=section_spans(body)
        wanted=generated_sections(generated[page],GENERATED_HEADINGS[page],current)
        draft_spans=section_spans(generated[page])
        for title,prior in old.items():
            candidate=wanted.get(title)
            if candidate==prior:continue
            span=spans.get(title)
            current_text=body[slice(*span)] if span else ''
            observed=digest(current_text.replace('\r\n','\n').encode())
            replacement=generated[page][slice(*draft_spans[title])] if candidate else ''
            proposals.append({'page':page,'section':title,'before':current_text,'after':replacement,
                              'page_sha256':digest(raw) if path else None,'prior':prior,'next':candidate,
                              'applicable':bool(span and (observed==prior['sha256'] or
                                                         (candidate and observed==candidate['sha256'])))})
    return proposals


def reassess(root, manifest):
    current=inventory(root)
    previous={item['path']:item for item in manifest.get('inventory',{}).get('sources',[])}
    latest={item['path']:item for item in current['sources']}
    changed=[{'path':path,'change':'added' if path not in previous else 'removed' if path not in latest else 'modified',
              'pages':[(WIKI/'architecture.md').as_posix()]+([(WIKI/'checks.md').as_posix()] if path=='package.json' or path.startswith('.github/') else [])}
             for path in sorted(previous.keys()|latest.keys()) if previous.get(path)!=latest.get(path)]
    recorded=[]
    observed_references=[]
    for source in manifest.get('references',[]):
        state=source_state(safe_file(root,source['path']))
        observed_references.append(state)
        if state!=source['sha256']:
            recorded.append({'path':source['path'],'page':source['page'],'change':'modified' if state else 'missing',
                             **({'section':source['summary']['section']} if source.get('summary') else {})})
    old_labels={entry['label'] for entry in previous.values()}
    observed={entry['label'] for entry in current['sources']}-{'Project documentation','Repository instructions'}
    choices=manifest.get('capability_decisions',{})
    capabilities=sorted(observed-old_labels)
    sections=section_refresh(root,manifest,current)
    return {'inventory':current,'changed':changed,'references_changed':recorded,'new_capabilities':capabilities,
            'sections':sections,
            'capability_suggestions':[{'key':key,'reason':CAPABILITY_GUIDANCE.get(key,'Confirm the component and its project-specific verification before using it in a claim.')}
                                      for key in sorted(observed) if key not in choices],
            'capability_decisions':choices,
            'plan_id':digest(json.dumps([manifest,current,observed_references,sections],sort_keys=True).encode())}


def knowledge_context(root, task):
    root=Path(root).resolve()
    if not safe_file(root,INDEX.as_posix()):return None
    paths=task.get('files',[])
    manifest=source_manifest(root)
    proposed=proposal_topic(task)
    topics=[]
    for source in manifest.get('references',[]):
        scope=source.get('scope') or [source['path']]
        if any(path==parent or path.startswith(parent.rstrip('/')+'/') for path in paths for parent in scope):
            page=source['page']
            if safe_file(root,page) and page not in topics:topics.append(page)
    if proposed and safe_file(root,proposed):topics.append(proposed)
    spec=SPEC_LINE.search(task.get('description') or '')
    if spec and safe_file(root,spec[1]):topics.insert(0,spec[1])
    if not topics and safe_file(root,(WIKI/'architecture.md').as_posix()):topics.append((WIKI/'architecture.md').as_posix())
    known={entry['path']:entry['sha256'] for entry in manifest.get('inventory',{}).get('sources',[])}
    commands=[entry for entry in manifest.get('inventory',{}).get('commands',[])
              if entry['command']=='npm run test' and
              source_state(safe_file(root,entry['source']))==known.get(entry['source']) and known.get(entry['source']) and
              any(path==entry['source'] or Path(path).suffix.lower() in ('.js','.ts','.jsx','.tsx') for path in paths)]
    return {'index':INDEX.as_posix(),'pages':list(dict.fromkeys(topics))[:3],
            **({'verification':commands[:1]} if commands else {})}


def proposal_topic(task):
    from wiki_knowledge import proposal_page
    page=proposal_page({'files':task.get('files',[])})
    return page.as_posix() if page else None


def specification_revision(root, page, section):
    page=wiki_path(page)
    path=safe_file(Path(root).resolve(),page.as_posix())
    if not path:raise ValueError('Specification page does not exist')
    return specification_body_revision(path.read_text(encoding='utf-8-sig'),section)


def wiki_section_body(text,section):
    if section:
        headings=list(re.finditer(r'^(#{1,6})\s+([^\n]+)$',text,re.M))
        slug=lambda heading:re.sub(r'[^\w -]','',heading.lower().strip()).replace(' ','-')
        for index,heading in enumerate(headings):
            if slug(heading[2])!=section:continue
            following=next((item.start() for item in headings[index+1:] if len(item[1])<=len(heading[1])),len(text))
            text=text[heading.start():following].rstrip()
            break
        else:raise ValueError('Specification section does not exist')
    return text


def specification_body_revision(text,section):
    return digest(wiki_section_body(text,section).encode())


def approved_wiki_changes(root,workspace,task,settings):
    """Report committed relevant knowledge added on the approved branch since divergence."""
    import git_sync
    import git_worktrees
    import subprocess

    if not root or not workspace or Path(root).resolve()==Path(workspace).resolve():return []
    root=Path(root).resolve()
    target=settings.get('git_base_branch')
    if not target:
        branch=git_worktrees.branch(root,git_worktrees.identity(task))
        target=git_sync.parent_branch(root,branch) if branch else None
    if not target:return []
    wanted={}
    spec=SPEC_LINE.search(task.get('description',''))
    if spec:wanted[spec[1]]=spec[2] or ''
    try:
        approved=git_sync._git(root,'rev-parse','--verify','refs/heads/'+target)
        current=git_sync._git(workspace,'rev-parse','--verify','HEAD')
        if approved.returncode or current.returncode:return []
        destination,head=approved.stdout.strip(),current.stdout.strip()
        base=git_sync._git(root,'merge-base',destination,head)
        if base.returncode or base.stdout.strip()==destination:return []
        ancestor=base.stdout.strip()
        committed=git_sync._git(root,'show',f'{destination}:{MANIFEST.as_posix()}')
        if not committed.returncode and len(committed.stdout.encode())<=MAX_FILE:
            try:references=json.loads(committed.stdout).get('references',[])
            except (ValueError,AttributeError):references=[]
            for reference in references:
                if not isinstance(reference,dict) or not isinstance(reference.get('page'),str):continue
                scope=reference.get('scope') or [reference.get('path')]
                if any(name==path or name.startswith(path.rstrip('/')+'/') for name in task.get('files',[])
                       for path in scope if isinstance(path,str) and path):
                    wanted.setdefault(reference['page'],'')

        def revision(commit,page,section):
            content=git_sync._git(root,'show',f'{commit}:{page}')
            if content.returncode:return None
            if len(content.stdout.encode())>MAX_FILE:raise ValueError('Wiki topic exceeds the read budget')
            try:return specification_body_revision(content.stdout.lstrip('\ufeff'),section)
            except ValueError:return None

        changes=[]
        for page,section in list(wanted.items())[:3]:
            page=wiki_path(page).as_posix()
            latest=revision(destination,page,section)
            if latest==revision(ancestor,page,section) or latest==revision(head,page,section):continue
            changes.append({'page':page,'section':section,'approved_sha':destination,'approved_branch':target,
                            'state':'updated' if latest else 'removed','revision':latest})
        return changes
    except (OSError,ValueError,subprocess.SubprocessError):
        return []


def bind_specification(root, description, spec):
    if not isinstance(spec,dict) or set(spec)-{'page','section'}:raise ValueError('Use a specification page and optional section')
    page=spec.get('page')
    section=spec.get('section','')
    if not isinstance(section,str) or len(section)>100 or (section and not re.fullmatch(r'[\w-]+',section)):
        raise ValueError('Use the Markdown heading anchor')
    revision=specification_revision(root,page,section)
    description=SPEC_LINE.sub('',description).rstrip()
    return description+f'\n\nSpecification: {page}'+(f'#{section}' if section else '')+f' @ {revision}'


def specification_status(root, description):
    match=SPEC_LINE.search(description or '')
    if not match:return None
    page,section,expected=match.groups()
    try:current=specification_revision(root,page,section)
    except (ValueError,OSError,UnicodeError):current=None
    return {'page':page,'section':section,'revision':expected,
            'state':'missing' if not current else 'current' if current==expected else 'changed'}


def specification_evidence_path(root, task_id):
    return Path(root)/'.scrum/local/specification-evidence'/f'{int(task_id)}.json'


def record_specification_evidence(root, workspace, task):
    from scrum_cli import atomic

    if not root or not workspace or not task.get('evidence') or not SPEC_LINE.search(task['description']):
        return
    specification=specification_status(workspace,task['description'])
    if specification['state']=='current':
        try:
            atomic(specification_evidence_path(root,task['id']),{
                'specification':{key:specification[key] for key in ('page','section','revision')},
                'evidence_sha256':digest(task['evidence'].encode()),
                'recorded_at':time.time(),
            })
        except OSError:
            pass


def specification_evidence(root, task, scope):
    if task['evidence']:
        try:
            path=specification_evidence_path(root,task['id'])
            record=json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size<=4096 else {}
        except (OSError,ValueError):
            record={}
        if (isinstance(record,dict) and record.get('specification')==scope and
            record.get('evidence_sha256')==digest(task['evidence'].encode()) and record.get('recorded_at')):
            return {'state':'requires_review','recorded_at':record['recorded_at'],
                    'reason':'Verification was recorded against this affected specification revision.'}
    return {'state':'unknown','reason':'No verification scope is recorded for this task, evidence and revision.'}


def specification_impact(db, project, prefix, root, args):
    page=args.get('page')
    if page is not None:page=wiki_path(page).as_posix()
    section=args.get('section')
    if section is not None and (not page or not isinstance(section,str) or len(section)>100 or
                                (section and not re.fullmatch(r'[\w-]+',section))):
        raise ValueError('Use a specification page and a valid heading anchor')
    after=args.get('after',0)
    limit=args.get('limit',15)
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=25:
        raise ValueError('Use a nonnegative cursor and a limit from 1 to 25')
    selector=f'Specification: {page}' if page else 'Specification: docs/agents/'
    groups={}
    revisions={}
    count=0
    for task in db.execute('SELECT id,number,title,status,description,evidence FROM tasks '
                           'WHERE project=? AND id>? AND instr(description,?)>0 ORDER BY id',(project,after,selector)):
        match=SPEC_LINE.search(task['description'])
        if not match:continue
        path,heading,pinned=match.groups()
        if (page and path!=page) or (section is not None and (heading or '')!=section):continue
        key=(path,heading)
        if key not in revisions:
            try:revisions[key]=specification_revision(root,path,heading)
            except (ValueError,OSError,UnicodeError):revisions[key]=None
        current=revisions[key]
        if pinned==current:continue
        if count==limit:
            return {'groups':list(groups.values()),'has_more':True,'next_after':after}
        group=groups.setdefault(key,{'page':path,'section':heading or '',
                                     'state':'changed' if current else 'missing','current_revision':current,'tasks':[]})
        scope={'page':path,'section':heading,'revision':pinned}
        group['tasks'].append({'id':task['id'],'code':f"{prefix}-{task['number']}",'title':task['title'],
                               'status':task['status'],'revision':pinned,'evidence':specification_evidence(root,task,scope)})
        count+=1
        after=task['id']
    return {'groups':list(groups.values()),'has_more':False,'next_after':after}


def skill_suggestions(root,task,client):
    """Match a small catalog to observed task scope and installed skill files."""
    if not root or not task:return []
    root=Path(root)
    home=Path.home()
    shared=(root/'.agents/skills',home/'.agents/skills')
    roots=shared+((root/'.codex/skills',Path(os.getenv('CODEX_HOME',home/'.codex'))/'skills') if client=='codex'
                  else (root/'.claude/skills',home/'.claude/skills'))
    files=task.get('files',[])
    text=' '.join([task.get('title',''),task.get('description',''),*task.get('acceptance_criteria',[])]).casefold()
    suggestions=[]

    def suggest(name,reason,paths):
        try:installed=any((folder/name/'SKILL.md').is_file() for folder in roots)
        except OSError:installed=False
        if installed:suggestions.append({'name':name,'reason':reason,'paths':paths[:2]})

    authoring=[name for name in files if PurePosixPath(name).name=='SKILL.md']
    if authoring:
        suggest('skill-creator','The task edits a skill definition.',authoring)
    frontend=[name for name in files if name.startswith(('public/','frontend/','web/')) and
              PurePosixPath(name).suffix in ('.html','.css','.js','.ts','.tsx','.jsx')]
    if frontend and re.search(r'\b(browser|ui|interface|web|navegador|interfaz)\b',text):
        suggest('playwright','The planned web files and acceptance call for a browser check.',frontend)
    source=[name for name in files if PurePosixPath(name).suffix in ('.py','.js','.ts','.tsx','.cs','.go','.rs')]
    if source and re.search(r'\b(callers?|dependencies|refactor|migration|architecture|impact|migración|dependencias|arquitectura)\b',text):
        suggest('codebase-memory','The task calls for structural tracing of source changes.',source)
    return suggestions[:2]


def worker_readiness(root, workspace, task, args, channel, stop='', unavailable='', wiki_changes=None):
    from mcp_selection import worker_readiness as effective_worker
    from scripts.verification_receipts import resume_checks

    def check(key,state,detail,next_action=None):
        checks.append({'key':key,'state':state,'detail':detail,
                       **({'next':next_action} if next_action else {})})

    has_root=bool(root and Path(root).is_dir())
    worker=effective_worker(args['client'],root,channel) if has_root else {
        'coordination':channel,'effective_specialists':[],'checks':[]}
    checks=worker.pop('checks')
    if stop:
        check('worker','blocked',stop,'Resolve the recorded Stop, quota or capacity condition before starting.')
    if not has_root:
        check('workspace','missing','The project folder is unavailable.','Open an available project folder.')
    else:
        root=Path(root)
        manifest=source_manifest(root)
        try:onboard=bool(manifest.get('onboarding') or load_state(root).get('phase')=='applied')
        except (OSError,ValueError,AttributeError):onboard=None
        if not onboard:
            check('onboarding','unknown' if onboard is None else 'missing',
                  'The onboarding record is unreadable.' if onboard is None else 'No accepted onboarding record.',
                  'Open Wiki → Project setup and review the plan.')
        else:
            check('onboarding','ready','Onboarding is applied.')
        if not safe_file(root,INDEX.as_posix()):
            check('wiki','missing','The wiki index is missing.','Open Wiki → Project setup or restore the versioned index.')
        else:
            knowledge=knowledge_context(workspace or root,task) if task else None
            pages=(knowledge or {}).get('pages',[])
            try:
                review=reassess(root,manifest)
                affected=[item for item in [*review['sections'],*review['references_changed']] if item['page'] in pages]
                issues=[item for item in diagnose(root) if item['path'] in {INDEX.as_posix(),*pages}]
            except (KeyError,OSError,TypeError,ValueError,AttributeError):
                check('wiki','unknown','The wiki sources or links could not be checked.','Use Wiki → Check links and Check sources.')
            else:
                if affected:
                    check('wiki','stale','A source backing a relevant wiki topic changed.','Use Wiki → Check sources to review the affected text.')
                elif issues:
                    check('wiki','stale','A link or topic in the relevant wiki is inconsistent.','Use Wiki → Check links to locate the problem.')
                else:
                    check('wiki','ready','Relevant wiki links and recorded topic sources passed the local checks.')
            worker['wiki_pages']=pages
    if not task:
        check('task','blocked',unavailable or 'No eligible unreserved task is available.',
              'Check the board for dependencies, existing claims and TODO work.')
    else:
        work=workspace or root
        if not workspace:
            check('workspace','unknown','The task worktree is unavailable.','Restore or open the recorded worktree before reusing evidence.')
        spec=specification_status(work,task['description'])
        if spec:
            level={'current':'ready','changed':'stale','missing':'missing'}[spec['state']]
            check('specification',level,spec['page']+(('#'+spec['section']) if spec['section'] else '')+' · '+spec['state'],
                  'Review the affected specification and update the task pin before proceeding.' if level!='ready' else None)
            worker['specification']=spec
        else:
            check('specification','ready','This task has no shared specification pin.')
        if not task.get('acceptance_criteria'):
            check('verification','missing','The task has no acceptance check.','Record an observable acceptance check before starting.')
        elif task.get('continuity') or task.get('evidence'):
            receipts=resume_checks(work,task['id']) if workspace else []
            states={item['status'] for item in receipts}
            state='stale' if 'changed' in states else 'unknown' if not states or 'unknown' in states else 'ready'
            if task.get('continuity_stale') or (spec and spec['state']!='current'):state='stale'
            worker['verification']=receipts
            check('verification',state,'Saved checks have been compared with their recorded inputs.' if workspace else 'No task worktree receipts are available.',
                  'Re-run or review only the checks whose recorded scope or inputs are unverified.' if state!='ready' else None)
        else:
            check('verification','ready','The task has acceptance criteria for its first verification.')
    statuses={item['state'] for item in checks}
    report={'worker':{'client':args['client'],'actor':args.get('actor')},
            'task':{key:task[key] for key in ('id','code','title','status')} if task else None,
            'state':'blocked' if 'blocked' in statuses else 'needs_attention' if statuses-{'ready'} else 'ready',
            'checks':checks,**worker}
    suggestions=skill_suggestions(root,task,args['client'])
    if suggestions:report['skill_suggestions']=suggestions
    if wiki_changes:report['approved_wiki_changes']=wiki_changes
    version=digest(json.dumps(report,sort_keys=True).encode())
    if args.get('version_seen')==version:
        return {'changed':False,'version':version}
    return {'changed':True,'version':version,**report}


def graph_readiness(root, workspace, task, args):
    from mcp_selection import graph_diagnostic

    if not root or not workspace or not Path(workspace).is_dir():
        return {'checks':[{'key':'workspace','state':'unknown','detail':'The requested checkout is unavailable.'}],
                'paths':[],'fallback':'Open the recorded worktree before inspecting source.'}
    requested=args.get('paths') or [name for name in task.get('files',[]) if safe_file(Path(workspace),name)][:6]
    return graph_diagnostic(workspace,requested)


def write_wiki(root, path, body):
    folder=(root/path).parent
    if folder.resolve()!=folder or not folder.is_relative_to(root/WIKI):
        raise ValueError('Wiki folder cannot be a link')
    folder.mkdir(parents=True,exist_ok=True)
    target=root/path
    handle,name=tempfile.mkstemp(prefix='.scrum-wiki-',dir=folder)
    try:
        with os.fdopen(handle,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name=='nt':os.rename(name,target)
            else:os.link(name,target)
        except FileExistsError:return False
        return True
    finally:
        Path(name).unlink(missing_ok=True)


def replace_page(path, text):
    handle,name=tempfile.mkstemp(prefix='.scrum-wiki-',dir=path.parent)
    try:
        with os.fdopen(handle,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(text)
        os.chmod(name,path.stat().st_mode)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def serialize(root, function):
    from contextlib import closing
    path=root/STATE.with_suffix('.lock.db')
    with closing(sqlite3.connect(path,timeout=2)) as lock,lock:
        lock.execute('BEGIN EXCLUSIVE')
        return function()


def pending_proposals(db,project,prefix,root,state):
    skipped=set(state.get('dismissed',[]))
    result=[]
    after=0
    while len(result)<25:
        page=proposals(db,project,prefix,root,{'after':after,'limit':25})
        result.extend(item for item in page['items'] if item['proposal_id'] not in skipped)
        if not page['has_more']:break
        after=page['next_after']
    return result[:25]


def source_summary(root, manifest, name, page):
    page=wiki_path(page).as_posix()
    entry=next((item for item in manifest.get('references',[]) if item['path']==name and item['page']==page),None)
    if not entry:
        raise ValueError('Link the source to this topic first')
    if not isinstance(name,str) or len(name)>240 or any(char in name for char in '\r\n<>'):
        raise ValueError('Use a short repository-relative source path')
    source=safe_file(root,name)
    target=safe_file(root,page)
    if not source or not target or source.samefile(target):
        raise ValueError('Use separate readable source and topic files')
    try:
        source_bytes=source.read_bytes()
        target_bytes=target.read_bytes()
        text=source_bytes.decode('utf-8-sig')
        body=target_bytes.decode('utf-8')
    except (OSError,UnicodeError) as error:
        raise ValueError('The source and topic must be readable UTF-8 text') from error
    section='Summary of '+name
    marker='scrum-source:'+digest(f'{page}\0{name}'.encode())[:24]
    start='<!-- '+marker+' -->'
    end='<!-- /'+marker+' -->'
    opened=body.count(start)
    closed=body.count(end)
    if opened!=closed or opened>1 or (opened and body.index(end)<=body.index(start)):
        raise ValueError('The source summary markers changed. Repair the topic before reviewing this source.')
    span=(body.index(start),body.index(end)+len(end)) if opened else None
    scope={'path':name,'page':page,'section':section,'source_sha256':digest(source_bytes),
           'page_sha256':digest(target_bytes)}
    review={**scope,'plan_id':digest(json.dumps([scope,entry],sort_keys=True).encode()),
            'draft':source_excerpt(text),'accepted':body[slice(*span)] if span else None}
    return review,entry,target,body,span,start,end


def operation(db, row, prefix, args):
    from store import actor_user
    from planning import apply as apply_tasks
    from scrum_cli import atomic

    project=args['project']
    root=Path(row['workspace_root']).resolve()
    if not row['workspace_root'] or not root.is_dir():raise ValueError('Project workspace is unavailable')
    action=args.get('action','view')
    if action=='view':
        name=args.get('page',INDEX.as_posix())
        section=args.get('section','')
        if section:
            if name==SKILLS_PAGE or len(section)>100 or not re.fullmatch(r'[\w-]+',section):
                raise ValueError('Use a wiki page and a valid heading anchor')
            relative=INDEX if name==INDEX.as_posix() else wiki_path(name)
            path=safe_file(root,relative.as_posix())
            if not path:
                raise ValueError('Wiki page does not exist')
            body=wiki_section_body(path.read_text(encoding='utf-8-sig'),section)
            return {'page':name,'section':section,'body':body}
        pages=topic_links(root,{})
        if name==SKILLS_PAGE:
            content={'body':None,'skills':project_skills(root)}
        else:
            relative=INDEX if name==INDEX.as_posix() else wiki_path(name)
            path=safe_file(root,relative.as_posix())
            content={'body':path.read_text(encoding='utf-8-sig') if path else None}
        saved=load_state(root)
        return {'pages':pages,'page':name,**content,
                'onboarding':saved.get('phase') or ('applied' if source_manifest(root).get('onboarding') else None),
                'sources':source_manifest(root).get('references',[]),
                'proposals':pending_proposals(db,project,prefix,root,saved),
                'learning':learning_page(saved)}
    if action=='learning':
        return learning_page(load_state(root),args.get('after',0))
    if action=='specification_impact':
        return specification_impact(db,project,prefix,root,args)
    if action=='source_draft':
        return source_summary(root,source_manifest(root),args.get('source'),args.get('page'))[0]
    if action=='wiki_revision':
        import git_sync
        revision=args.get('revision','')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',revision):
            raise ValueError('Use a specific Git commit for the wiki revision')
        page=wiki_path(args.get('page')).as_posix()
        result=git_sync._git(root,'show',f'{revision}:{page}')
        if result.returncode or len(result.stdout.encode())>MAX_FILE:
            raise ValueError('The committed wiki page is unavailable at that revision')
        return {'page':page,'revision':revision,'body':result.stdout}
    if action=='source':
        name=args.get('source','')
        if not isinstance(name,str):raise ValueError('Supply a project source path')
        sources=source_manifest(root)
        allowed=set(SOURCES)|{source['path'] for source in sources.get('references',[])+sources.get('inventory',{}).get('sources',[])}
        path=safe_file(root,name) if name in allowed or project_skill_manifest(root,name) else None
        if not path:raise ValueError('Source is unavailable or has not been linked to the wiki')
        return {'path':name,'body':path.read_text(encoding='utf-8-sig')}
    if action=='reassess':return reassess(root,source_manifest(root))
    if action=='diagnose':return {'issues':diagnose(root)}
    if action=='status':
        saved=load_state(root)
        if not saved.get('phase') and source_manifest(root).get('onboarding'):
            return {'phase':'applied','plan':None}
        plan=saved.get('accepted') if saved.get('phase')=='applying' else (
            review_plan(root,row['name'],saved) if saved.get('inventory') and saved.get('phase')=='draft' else None)
        return {**saved,'questions':questions_for(saved.get('inventory',{})),'plan':plan}
    actor=actor_user(db,args)
    if actor['kind']!='human':raise ValueError('Only a human can change onboarding or review wiki proposals')
    saved=load_state(root)
    if action in ('learning_propose','learning_review'):
        item=learning_update(root,saved,args,actor['id'],db,project,prefix)
        atomic(root/STATE,saved)
        return item
    if action=='source_publish':
        manifest=source_manifest(root)
        review,entry,path,body,span,start,end=source_summary(root,manifest,args.get('source'),args.get('page'))
        if args.get('plan_id')!=review['plan_id']:
            raise ValueError('Source or wiki topic changed. Open a fresh draft before publishing.')
        text=args.get('text')
        if not isinstance(text,str) or not text.strip() or len(text)>1500 or 'scrum-source:' in text:
            raise ValueError('Supply a reviewed summary of at most 1500 characters')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM tasks WHERE project=? AND lock_until>?',(project,time.time())).fetchone():
            raise ValueError('Finish or release active claims before editing the wiki from the web')
        if source_summary(root,source_manifest(root),review['path'],review['page'])[0]['plan_id']!=review['plan_id']:
            raise ValueError('Source or wiki topic changed. Open a fresh draft before publishing.')
        link=os.path.relpath(review['path'],PurePosixPath(review['page']).parent).replace(os.sep,'/')
        reviewed=text.strip().replace('\r\n','\n').replace('\r','\n')
        block=f"{start}\n\n## {review['section']}\n\n{reviewed}\n\n[Source]({quote(link)}) · Revision `{review['source_sha256']}`\n\n{end}"
        if '\r\n' in body:block=block.replace('\n','\r\n')
        if span:
            updated=body[:span[0]]+block+body[span[1]:]
        else:
            newline='\r\n' if '\r\n' in body else '\n'
            updated=body+newline*2+block+newline
        replace_page(path,updated)
        entry['sha256']=review['source_sha256']
        entry['summary']={'section':review['section']}
        write_manifest(root,manifest)
        return {'published':True,'page':review['page'],'section':review['section'],'source_sha256':entry['sha256']}
    if action=='capability':
        if not safe_file(root,INDEX.as_posix()):raise ValueError('Set up the project wiki first')
        manifest=source_manifest(root)
        review=reassess(root,manifest)
        if args.get('plan_id')!=review['plan_id']:raise ValueError('Review the current capability suggestions')
        key=args.get('capability')
        decision=args.get('decision')
        if decision not in ('accepted','deferred') or key not in {item['key'] for item in review['capability_suggestions']}:
            raise ValueError('Choose an available capability and decide to accept or defer it')
        manifest.setdefault('capability_decisions',{})[key]=decision
        write_manifest(root,manifest)
        return {'capability':key,'decision':decision}
    if action in ('ingest','refresh'):
        manifest=source_manifest(root)
        if not safe_file(root,INDEX.as_posix()):raise ValueError('Set up the project wiki first')
        if action=='ingest':
            name=args.get('source','')
            source=safe_file(root,name) if isinstance(name,str) else None
            page=wiki_path(args.get('page'))
            if not source or not safe_file(root,page.as_posix()):raise ValueError('Use an existing project source and wiki topic')
            text=args.get('text','')
            scope=args.get('scope',[])
            if not isinstance(text,str) or len(text)>1000 or not isinstance(scope,list) or len(scope)>12 or any(
                not isinstance(path,str) or not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or '\\' in path for path in scope
            ):raise ValueError('Use a short note and up to twelve relative paths')
            previous=next((item for item in manifest.get('references',[]) if (item['path'],item['page'])==(name,page.as_posix())),{})
            entry={**previous,'path':name,'page':page.as_posix(),'note':text,'scope':scope}
            entry.setdefault('sha256',source_state(source))
            refs=[item for item in manifest.get('references',[]) if (item['path'],item['page'])!=(name,page.as_posix())]
            if len(refs)>=100:raise ValueError('Keep a maximum of 100 linked sources')
            manifest['references']=refs+[entry]
            write_manifest(root,manifest)
            return entry
        review=reassess(root,manifest)
        if args.get('plan_id')!=review['plan_id']:raise ValueError('Review the current source changes before refreshing')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM tasks WHERE project=? AND lock_until>?',(project,time.time())).fetchone():
            raise ValueError('Finish or release active claims before refreshing the wiki')
        if reassess(root,source_manifest(root))['plan_id']!=review['plan_id']:
            raise ValueError('Sources or wiki pages changed; review a fresh preview')
        provenance=section_provenance(root,manifest)
        updates={}
        retained=[]
        for item in review['sections']:
            if item['applicable']:
                updates.setdefault(item['page'],[]).append(item)
            else:
                retained.append(item['page']+'#'+item['section'])
        updated=[]
        for name,items in updates.items():
            path=safe_file(root,name)
            raw=path.read_bytes() if path else b''
            if not path or digest(raw)!=items[0]['page_sha256']:
                raise ValueError('Wiki page changed; review a fresh preview')
            body=raw.decode('utf-8')
            spans=section_spans(body)
            for item in sorted(items,key=lambda item:spans[item['section']][0],reverse=True):
                title=item['section']
                start,end=spans[title]
                replacement=item['after'].replace('\n','\r\n') if '\r\n' in body[start:end] else item['after']
                body=body[:start]+replacement+body[end:]
                if item['next']:
                    provenance[name][title]=item['next']
                else:
                    provenance[name].pop(title)
            if raw!=body.encode():
                replace_page(path,body)
                updated.append(name)
        manifest['generated_sections']=provenance
        manifest['inventory']=review['inventory']
        write_manifest(root,manifest)
        return {'updated':updated,'retained_for_review':retained}
    if action in ('publish','dismiss'):
        item=next((item for item in pending_proposals(db,project,prefix,root,saved)
                   if item['proposal_id']==args.get('proposal_id')),None)
        if item is None:raise ValueError('This proposal is no longer pending')
        if action=='dismiss':
            saved['dismissed']=(saved.get('dismissed',[])+[item['proposal_id']])[-500:]
            atomic(root/STATE,saved)
            return {'dismissed':True}
        body=args.get('text',item['draft_markdown'])
        if not isinstance(body,str) or not body.strip() or len(body)>5000:raise ValueError('Supply the reviewed Markdown')
        path=safe_file(root,item['page'])
        if not path or args.get('page_sha256')!=source_state(path):raise ValueError('Wiki page changed; reload the proposal')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM tasks WHERE project=? AND lock_until>?',(project,time.time())).fetchone():
            raise ValueError('Finish or release active claims before editing the wiki from the web')
        marker=item['draft_markdown'].splitlines()[0]
        text=path.read_text(encoding='utf-8-sig')
        replace_page(path,text.rstrip()+'\n\n'+(marker+'\n\n' if marker not in body else '')+body.strip()+'\n')
        return {'published':True,'page':item['page']}
    if action=='prepare':
        if saved.get('phase') in ('applying','applied') or source_manifest(root).get('onboarding'):
            raise ValueError('This project already has an accepted onboarding plan')
        saved={**saved,'phase':'draft','inventory':inventory(root),'answers':answers_from(args.get('answers',saved.get('answers',{})))}
        atomic(root/STATE,saved)
        return {'phase':'draft','questions':questions_for(saved['inventory']),'plan':review_plan(root,row['name'],saved)}
    if action!='apply':raise ValueError('Unknown project knowledge action')
    if saved.get('phase')=='applied':
        if args.get('plan_id')!=saved['plan_id']:raise ValueError('This project already has an accepted onboarding plan')
        return saved
    if not saved.get('inventory'):raise ValueError('Prepare the onboarding plan first')
    if saved.get('phase')=='applying':
        plan=saved['accepted']
    else:
        plan=review_plan(root,row['name'],saved)
        if inventory(root)!=saved['inventory']:raise ValueError('Project sources changed; prepare a fresh preview')
    if args.get('plan_id')!=plan['plan_id'] or not plan['ready']:raise ValueError('Review the current plan and answer the goal, milestone and acceptance questions')
    saved.update(phase='applying',accepted=plan)
    atomic(root/STATE,saved)
    for path,body in plan['pages'].items():
        if path in plan['updates']:
            current=safe_file(root,path)
            if not current:raise ValueError(f'Wiki page disappeared: {path}')
            if current.read_text(encoding='utf-8-sig')==body:continue
            if source_state(current)!=plan['existing'][path]:raise ValueError(f'Wiki page changed while applying: {path}')
            replace_page(current,body)
        if plan['existing'][path] is None:
            if not write_wiki(root,Path(path),body):
                current=safe_file(root,path)
                if not current or current.read_text(encoding='utf-8')!=body:raise ValueError(f'Wiki page changed while applying: {path}')
    answers=plan['answers']
    milestone=answers['milestone']
    result=apply_tasks(db,{'project':project,'actor':actor['id'],'items':[{
        'key':'first-milestone','title':milestone[:120],'description':milestone+'\n\nProject goal: '+answers['goal']+
        ('\nConstraints: '+answers['constraints'] if answers.get('constraints') else '')+'\n\nProject context: docs/agents/project.md.',
        'acceptance_criteria':[answers['acceptance']],'scope_size':'Small',
        **({'specification':{'page':'docs/agents/project.md','section':'first-milestone'}}
           if plan['existing']['docs/agents/project.md'] is None else {}),
        'scope_reason':'One first milestone with a concrete acceptance check'}]},project)
    db.commit()
    manifest=source_manifest(root)
    manifest.update(onboarding=plan['plan_id'],inventory=plan['inventory'],
                    generated={path:source_state(safe_file(root,path)) for path in plan['pages'] if plan['existing'][path] is None})
    manifest['generated_sections']={
        page:generated_sections(plan['pages'][page],titles,plan['inventory'])
        for page,titles in GENERATED_HEADINGS.items() if page in manifest['generated']
    }
    write_manifest(root,manifest)
    saved={**saved,'phase':'applied','plan_id':plan['plan_id'],'inventory':plan['inventory'],'answers':answers,'tasks':result['items']}
    saved.pop('accepted',None)
    atomic(root/STATE,saved)
    return saved
