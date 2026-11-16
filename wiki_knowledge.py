"""Read-only diagnostics and review proposals for the repository wiki."""
import hashlib
import re
import subprocess
import time
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


WIKI = PurePosixPath('docs/agents')
INDEX = WIKI / 'index.md'
LINK = re.compile(r'\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s()]+))(?=\s|\))|^\s{0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))|<(git:[^>\n]+)>', re.M)
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def source_excerpt(text):
    """Create a short editable extract from the source's introductory prose."""
    paragraphs=[]
    fence=None
    for line in text.splitlines():
        marker=FENCE.match(line)
        if marker:
            delim=marker[1]
            if not fence:fence=delim
            elif delim.startswith(fence) and not line[marker.end():].strip():fence=None
            continue
        stripped=line.strip()
        if fence or not stripped or stripped.startswith(('#','<!--','---','|')):
            continue
        paragraphs.append(stripped)
        if sum(len(item)+1 for item in paragraphs)>700:break
    draft='\n'.join(paragraphs)
    if len(draft)>700:
        cut=draft[:697].rsplit(None,1)[0]
        draft=cut+'…'
    return draft


def wiki_path(value):
    if not isinstance(value, str) or len(value)>240 or any(c in value for c in ('\\',':','\x00')):
        raise ValueError('Use a relative Markdown page under docs/agents')
    path=PurePosixPath(value)
    if not path.is_relative_to(WIKI) or path==INDEX or path.suffix!='.md' or path.as_posix()!=value or '..' in path.parts:
        raise ValueError('Use an existing topic page under docs/agents')
    return path


def wiki_file(root, relative):
    path=(root/relative).resolve()
    return path if path.is_relative_to(root/WIKI) and path.is_file() else None


def visible_lines(text, *, hide_inline=True):
    fence=None
    for line in text.splitlines():
        match=FENCE.match(line)
        if fence:
            if match and match[1][0]==fence[0] and len(match[1])>=len(fence) and not line[match.end():].strip():
                fence=None
            yield ''
        elif match:
            fence=match[1]
            yield ''
        else:
            yield re.sub(r'`+[^`]*`+', '', line) if hide_inline else line


def markdown_anchors(path):
    anchors=set()
    for line in visible_lines(path.read_text(encoding='utf-8-sig'),hide_inline=False):
        anchors.update(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']',line))
        heading=re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*$',line)
        if heading:
            slug=re.sub(r'[^\w -]','',heading[1].strip().lower()).replace(' ','-')
            unique=slug
            index=1
            while unique in anchors:
                unique=f'{slug}-{index}'
                index+=1
            anchors.add(unique)
    return anchors


def git_reference(root, ref):
    if not ref or ref.startswith('-') or any(c.isspace() for c in ref):
        return False
    try:
        result=subprocess.run(['git','-C',str(root),'rev-parse','--verify','--quiet','--end-of-options',ref+'^{commit}'],
                              capture_output=True,timeout=5)
    except (OSError,subprocess.SubprocessError):
        return False
    return result.returncode==0


def diagnose(root, *, check_entry=True):
    root=Path(root).resolve()
    issues=[]
    indexed=set()
    anchors={}
    refs={}
    pages=sorted(path for path in (root/WIKI).rglob('*.md') if path.resolve().is_relative_to(root/WIKI))
    def issue(path,line,kind,target):
        issues.append({'path':path.relative_to(root).as_posix(),'line':line,'kind':kind,'target':target})
    index=root/INDEX
    if not index.is_file():
        issue(index,1,'missing_index',INDEX.as_posix())
    if check_entry and (root/'AGENTS.md').is_file():
        pages.append(root/'AGENTS.md')
    for page in pages:
        try:
            body='\n'.join(visible_lines(page.read_text(encoding='utf-8-sig')))
        except (OSError,UnicodeError):
            issue(page,1,'unreadable_page','')
            continue
        for match in LINK.finditer(body):
            target=next(group for group in match.groups() if group is not None)
            line=body.count('\n',0,match.start())+1
            if target.startswith('git:'):
                ref=unquote(target[4:])
                if ref not in refs:refs[ref]=git_reference(root,ref)
                if not refs[ref]:issue(page,line,'unresolved_git_reference',target)
                continue
            try:parts=urlsplit(target)
            except ValueError:
                issue(page,line,'invalid_link',target)
                continue
            if parts.scheme or parts.netloc:continue
            name=unquote(parts.path)
            try:
                path=((root/name.lstrip('/')) if name.startswith('/') else (page.parent/name if name else page)).resolve()
            except (OSError,ValueError):
                issue(page,line,'invalid_link',target)
                continue
            if not path.is_relative_to(root) or not path.exists():
                issue(page,line,'broken_link',target)
                continue
            if page==index:indexed.add(path)
            fragment=unquote(parts.fragment)
            if fragment and path.suffix.lower()=='.md':
                if path not in anchors:
                    try:anchors[path]=markdown_anchors(path)
                    except (OSError,UnicodeError):anchors[path]=None
                if anchors[path] is None:issue(page,line,'unreadable_target',target)
                elif fragment not in anchors[path]:issue(page,line,'missing_anchor',target)
    for page in pages:
        if page not in (index,root/'AGENTS.md') and page.resolve() not in indexed:
            issue(page,1,'unindexed_page',INDEX.as_posix())
    return issues


def proposal_page(item):
    if 'wiki_page' in item:return wiki_path(item['wiki_page'])
    topics=set()
    for name in item['files']:
        filename=PurePosixPath(name).name
        if filename.startswith('git_'):
            topics.add('git')
        elif filename in {'verify.py', 'verification_receipts.py', 'eval_worker.py', 'usage_audit.py', 'overhead.py', 'wiki_knowledge.py', 'wiki_doctor.py'}:
            topics.add('verification')
        elif filename in {'agent_bridge.py', 'context_tools.py', 'context_batches.py', 'workflow.py', 'scrum_cli.py', 'task_decisions.py'}:
            topics.add('coordination')
    if len(topics)==1:return WIKI/(topics.pop()+'.md')
    return None


def proposals(db, project, prefix, workspace, args):
    import task_decisions

    after=args.get('after',0)
    limit=args.get('limit',10)
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=25:
        raise ValueError('Invalid wiki proposal page')
    task=args.get('id')
    if task is not None and not db.execute('SELECT 1 FROM tasks WHERE project=? AND id=?',(project,task)).fetchone():
        raise ValueError('Task not found')
    root=Path(workspace).resolve()
    if not workspace or not wiki_file(root,INDEX):
        return {'items':[],'has_more':False,'next_after':after}
    pages={}
    items=[]
    for item in reversed(task_decisions.active(db,project,prefix)):
        if task is not None and task!=item['source_task']:continue
        page=proposal_page(item)
        path=wiki_file(root,page) if page else None
        if not path:continue
        fingerprint=hashlib.sha256((item['source_code']+'\0'+item['ref']+'\0'+item['decision']).encode()).hexdigest()[:24]
        marker=f'<!-- scrum-wiki:{fingerprint} -->'
        if path not in pages:
            try:pages[path]=path.read_text(encoding='utf-8-sig')
            except (OSError,UnicodeError):pages[path]=None
        if pages[path] is None:continue
        if marker in pages[path]:continue
        row=db.execute('SELECT evidence FROM tasks WHERE project=? AND id=?',(project,item['source_task'])).fetchone()
        evidence=row['evidence'].strip()
        if not evidence:continue
        items.append({
            'proposal_id':fingerprint,
            'page_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'page':page.as_posix(),
            'source_task':item['source_task'],
            'source_code':item['source_code'],
            'decision_ref':item['ref'],
            'evidence_excerpt':evidence[:500],
            'draft_markdown':f"{marker}\n\n{item['decision']}\n\n"
                             f"{item['reason']}\n\nSource: Scrum task `{item['source_code']}` (`{item['ref']}`).",
        })
        if len(items)>after+limit:break
    visible=items[after:after+limit]
    return {'items':visible,'has_more':len(items)>after+limit,'next_after':after+len(visible)}


def learning_page(state, after=0):
    if type(after) is not int or after<0:
        raise ValueError('Use a nonnegative learning proposal cursor')
    records=state.get('learning',[])
    visible=list(reversed(records))[after:after+15]
    return {'items':visible,'has_more':after+len(visible)<len(records),'next_after':after+len(visible)}


def learning_update(root, state, args, actor, db, project, prefix):
    """Promote confirmed local evidence without modifying the source or policy."""
    from usage_audit import verification_events
    import git_revert_evidence

    key=args.get('proposal_id')
    if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{24}',key):
        raise ValueError('Select a confirmed verification failure or delivery revert from the usage audit')
    entries=state.setdefault('learning',[])
    existing=next((item for item in entries if item['proposal_id']==key),None)
    if args['action']=='learning_propose':
        if existing:
            return existing
        matches=verification_events(root,pattern_id=key)['patterns']
        if not matches:
            matches=git_revert_evidence.patterns(db,project,prefix,root,pattern_id=key)['patterns']
        if not matches or matches[0]['kind'] not in ('verification_failure','git_revert'):
            raise ValueError('No confirmed verification failure or delivery revert remains in the recorded evidence')
        pattern=matches[0]
        record={
            'proposal_id':key,'status':'pending','created_at':time.time(),'created_by':actor,
            'failure_kind':pattern['failure_kind'],'scope':pattern['scope'],'recommendation':pattern['action'],
            'evidence':{field:pattern[field] for field in ('pattern_id','observations','first_at','last_at')},
        }
        if pattern['kind']=='git_revert':record['evidence']['verification_evidence']=pattern['verification_evidence']
        entries.append(record)
        return record
    decision=args.get('decision')
    if existing is None or decision not in ('accepted','dismissed'):
        raise ValueError('Choose a learning proposal and accept or dismiss it')
    if existing['status']==decision:
        return existing
    if existing['status']!='pending':
        raise ValueError('This learning proposal has already been reviewed')
    existing.update(status=decision,reviewed_at=time.time(),reviewed_by=actor)
    return existing
