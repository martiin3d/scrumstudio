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

from wiki_knowledge import INDEX, WIKI, diagnose, proposals, wiki_file, wiki_path


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
STATE = Path('.scrum/local/knowledge-onboarding.json')
MANIFEST = WIKI/'sources.json'
MAX_FILE = 128 * 1024
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
    return {key:text.strip() for key,text in value.items()}


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


def reassess(root, manifest):
    current=inventory(root)
    previous={item['path']:item for item in manifest.get('inventory',{}).get('sources',[])}
    latest={item['path']:item for item in current['sources']}
    changed=[{'path':path,'change':'added' if path not in previous else 'removed' if path not in latest else 'modified',
              'pages':[(WIKI/'architecture.md').as_posix()]+([(WIKI/'checks.md').as_posix()] if path=='package.json' or path.startswith('.github/') else [])}
             for path in sorted(previous.keys()|latest.keys()) if previous.get(path)!=latest.get(path)]
    recorded=[]
    for source in manifest.get('references',[]):
        state=source_state(safe_file(root,source['path']))
        if state!=source['sha256']:recorded.append({'path':source['path'],'page':source['page'],'change':'modified' if state else 'missing'})
    old_labels={entry['label'] for entry in previous.values()}
    capabilities=sorted({entry['label'] for entry in current['sources']}-old_labels-{'Project documentation','Repository instructions'})
    return {'inventory':current,'changed':changed,'references_changed':recorded,'new_capabilities':capabilities,
            'plan_id':digest(json.dumps([manifest,current],sort_keys=True).encode())}


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
    text=path.read_text(encoding='utf-8-sig')
    if section:
        headings=list(re.finditer(r'^(#{1,6})\s+([^\n]+)$',text,re.M))
        slug=lambda heading:re.sub(r'[^\w -]','',heading.lower().strip()).replace(' ','-')
        for index,heading in enumerate(headings):
            if slug(heading[2])!=section:continue
            following=next((item.start() for item in headings[index+1:] if len(item[1])<=len(heading[1])),len(text))
            text=text[heading.start():following].rstrip()
            break
        else:raise ValueError('Specification section does not exist')
    return digest(text.encode())


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


def operation(db, row, prefix, args):
    from store import actor_user
    from planning import apply as apply_tasks
    from scrum_cli import atomic

    project=args['project']
    root=Path(row['workspace_root']).resolve()
    if not row['workspace_root'] or not root.is_dir():raise ValueError('Project workspace is unavailable')
    action=args.get('action','view')
    if action=='view':
        pages=topic_links(root,{})
        name=args.get('page',INDEX.as_posix())
        relative=INDEX if name==INDEX.as_posix() else wiki_path(name)
        path=safe_file(root,relative.as_posix())
        saved=load_state(root)
        return {'pages':pages,'page':name,'body':path.read_text(encoding='utf-8-sig') if path else None,
                'onboarding':saved.get('phase') or ('applied' if source_manifest(root).get('onboarding') else None),
                'sources':source_manifest(root).get('references',[]),
                'proposals':pending_proposals(db,project,prefix,root,saved)}
    if action=='source':
        name=args.get('source','')
        if not isinstance(name,str):raise ValueError('Supply a project source path')
        sources=source_manifest(root)
        allowed=set(SOURCES)|{source['path'] for source in sources.get('references',[])+sources.get('inventory',{}).get('sources',[])}
        path=safe_file(root,name) if name in allowed else None
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
        return {**saved,'questions':dict(QUESTIONS),'plan':plan}
    actor=actor_user(db,args)
    if actor['kind']!='human':raise ValueError('Only a human can change onboarding or review wiki proposals')
    saved=load_state(root)
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
            entry={'path':name,'page':page.as_posix(),'sha256':source_state(source),'note':text,'scope':scope}
            refs=[item for item in manifest.get('references',[]) if (item['path'],item['page'])!=(name,page.as_posix())]
            if len(refs)>=100:raise ValueError('Keep a maximum of 100 linked sources')
            manifest['references']=refs+[entry]
            write_manifest(root,manifest)
            return entry
        review=reassess(root,manifest)
        if args.get('plan_id')!=review['plan_id']:raise ValueError('Review the current source changes before refreshing')
        generated=draft(root,row['name'],review['inventory'],saved.get('answers',{}))
        updated=[];retained=[]
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM tasks WHERE project=? AND lock_until>?',(project,time.time())).fetchone():
            raise ValueError('Finish or release active claims before refreshing the wiki')
        for name in ((WIKI/'architecture.md').as_posix(),(WIKI/'checks.md').as_posix()):
            path=safe_file(root,name)
            expected=manifest.get('generated',{}).get(name)
            if not path or not expected or source_state(path)!=expected:
                retained.append(name)
                continue
            replace_page(path,generated[name])
            manifest['generated'][name]=source_state(path)
            updated.append(name)
        if not retained:manifest['inventory']=review['inventory']
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
        if saved.get('phase') in ('applying','applied'):raise ValueError('This project already has an accepted onboarding plan')
        saved={**saved,'phase':'draft','inventory':inventory(root),'answers':answers_from(args.get('answers',saved.get('answers',{})))}
        atomic(root/STATE,saved)
        return {'phase':'draft','questions':dict(QUESTIONS),'plan':review_plan(root,row['name'],saved)}
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
        'acceptance_criteria':[answers['acceptance'][:500]],'scope_size':'Small',
        **({'specification':{'page':'docs/agents/project.md','section':'first-milestone'}}
           if plan['existing']['docs/agents/project.md'] is None else {}),
        'scope_reason':'One first milestone with a concrete acceptance check'}]},project)
    db.commit()
    manifest=source_manifest(root)
    manifest.update(onboarding=plan['plan_id'],inventory=plan['inventory'],
                    generated={path:source_state(safe_file(root,path)) for path in plan['pages'] if plan['existing'][path] is None})
    write_manifest(root,manifest)
    saved={**saved,'phase':'applied','plan_id':plan['plan_id'],'inventory':plan['inventory'],'answers':answers,'tasks':result['items']}
    saved.pop('accepted',None)
    atomic(root/STATE,saved)
    return saved
