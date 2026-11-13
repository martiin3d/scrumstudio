"""Install bundled skills and register the local MCP using local paths."""
import argparse
import base64
import hashlib
import json
from datetime import datetime
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]


def validate_managed_path(path,root):
    if not path.is_relative_to(root):
        raise ValueError('Managed path escapes its installation directory')
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():
            raise ValueError('Repair refuses linked paths')
        if part==root:break

def skill_manifest(folder):
    result={}
    for path in [folder,*folder.rglob('*')]:
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
            raise ValueError('Skill repair does not follow symlinks or junctions')
        if path.is_file():result[path.relative_to(folder).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def repair_skill(name,profile,config_root,backup):
    """Explicitly relocate one legacy skill; never overwrite a different skill."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',name):
        raise ValueError('Skill repair requires a simple skill folder name')
    source=config_root/'skills'/name
    target=profile/'.agents/skills'/name
    if not source.exists():
        if (target/'SKILL.md').is_file():
            skill_manifest(target)
            return 'already_canonical'
        raise ValueError('No installed legacy or canonical skill found: '+name)
    if not (source/'SKILL.md').is_file():raise ValueError('Legacy skill has no SKILL.md: '+name)
    original=skill_manifest(source)
    if target.exists() and skill_manifest(target)!=original:
        raise ValueError('Canonical skill differs; preserved both copies: '+name)
    archive=backup/'legacy-skills'/name
    # Verify resolved recursive copy/move targets before changing any directory.
    if not source.resolve().is_relative_to((config_root/'skills').resolve()) or not target.resolve().is_relative_to((profile/'.agents/skills').resolve()) or not archive.resolve().is_relative_to(backup.resolve()):
        raise ValueError('Skill repair paths escaped their intended directories')
    if archive.exists():raise ValueError('Legacy skill archive already exists')
    if not target.exists():shutil.copytree(source,target)
    if skill_manifest(target)!=original:raise RuntimeError('Repaired skill content verification failed')
    archive.parent.mkdir(parents=True,exist_ok=True)
    shutil.move(str(source),str(archive))
    return 'repaired'


def file_value(path):
    if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
        raise ValueError('Repair refuses linked paths')
    return path.read_bytes() if path.exists() else None


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def repair_plan(clients,profile,config_root,workspace):
    """A deterministic, read-only snapshot of managed files and proposed repairs."""
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    import mcp_selection
    changes=[];findings={}
    def add(path,current,desired):
        if current!=desired:
            changes.append({'path':str(path),'before':digest(current),'after':digest(desired),
                            'current':current,'desired':desired})
    for client in clients:
        target_root=profile/('.agents' if client=='codex' else '.claude')/'skills'
        mismatches=[]
        for source in sorted((ROOT/'skills').iterdir()):
            if not (source/'SKILL.md').is_file():continue
            target=target_root/source.name
            validate_managed_path(target,profile)
            if not target.resolve().is_relative_to(target_root.resolve()):
                raise ValueError('Managed skill escapes its installation directory')
            installed=skill_manifest(target) if target.exists() else {}
            expected=skill_manifest(source)
            if any(installed.get(path)!=sha for path,sha in expected.items()):
                mismatches.append({'skill':source.name,'status':'drifted' if installed else 'missing'})
                for path in expected:
                    if installed.get(path)!=expected[path]:
                        add(target/path,file_value(target/path),(source/path).read_bytes())
        try:selection=mcp_selection.effective(client,workspace,profile)
        except ValueError as error:selection={'error':str(error)}
        findings[client]={'skills':mismatches,'mcp':selection}
        if client=='codex':
            findings[client]['duplicate_skills']=[source.name for source in sorted((ROOT/'skills').iterdir())
                if (source/'SKILL.md').is_file() and (config_root/'skills'/source.name/'SKILL.md').is_file()
                and (target_root/source.name/'SKILL.md').is_file()]
        if client=='claude':
            path=profile/'.claude/settings.json'
            validate_managed_path(path,profile)
            raw=file_value(path)
            settings=json.loads(raw) if raw else {}
            hooks=settings.get('hooks',{})
            duplicates={}
            for event in HOOK_EVENTS:
                groups=hooks.get(event,[])
                count=sum('claude_hooks.py' in hook.get('command','') for group in groups for hook in group.get('hooks',[]))
                if count>1:duplicates[event]=count
            findings[client]['duplicate_hooks']=duplicates
            if duplicates:
                add(path,raw,json.dumps(merged_claude_hooks(settings,events=duplicates),indent=2).encode('utf-8'))
    public=[{key:change[key] for key in ('path','before','after')} for change in changes]
    plan={'clients':clients,'workspace':str(workspace),'changes':public}
    return {'plan_id':digest(json.dumps(plan,sort_keys=True).encode()),**plan,'findings':findings},changes


def write_managed_file(path,value):
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    handle,name=tempfile.mkstemp(prefix='.scrum-repair-',dir=path.parent)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def apply_repair(plan,changes,accepted,backups):
    if accepted!=plan['plan_id']:raise ValueError('Repair plan changed; run doctor again')
    for change in changes:
        if digest(file_value(Path(change['path'])))!=change['before']:
            raise ValueError('Managed file changed after preview')
    if not changes:return {'changed':0,'backup':None}
    backups.mkdir(parents=True,exist_ok=True)
    backup=Path(tempfile.mkdtemp(prefix='repair-',dir=backups))
    entries=[{**item,'original':base64.b64encode(change['current']).decode() if change['current'] is not None else None}
             for item,change in zip(plan['changes'],changes)]
    write_managed_file(backup/'manifest.json',json.dumps({'plan_id':accepted,'entries':entries}).encode())
    written=[]
    try:
        for change in changes:
            path=Path(change['path'])
            if digest(file_value(path))!=change['before']:raise ValueError('Managed file changed after preview')
            write_managed_file(path,change['desired']);written.append(change)
            if digest(file_value(path))!=change['after']:raise ValueError('Repair verification failed')
    except BaseException:
        for change in reversed(written):
            path=Path(change['path'])
            if digest(file_value(path))==change['after']:write_managed_file(path,change['current'])
        raise
    return {'changed':len(changes),'backup':backup.name}


def restore_repair(backups,name,profile):
    if not re.fullmatch(r'repair-[a-z0-9_]+',name):raise ValueError('Supply a repair backup ID')
    location=backups/name/'manifest.json'
    if not location.resolve().is_relative_to(backups.resolve()):raise ValueError('Backup escapes its directory')
    saved=json.loads(location.read_text(encoding='utf-8'))
    roots=[profile/'.agents/skills',profile/'.claude/skills']
    changes=[]
    for item in saved['entries']:
        path=Path(item['path'])
        validate_managed_path(path,profile)
        managed_skill=any(path.resolve().is_relative_to(root.resolve()) for root in roots)
        if not managed_skill and path!=profile/'.claude/settings.json':raise ValueError('Invalid restore target')
        original=base64.b64decode(item['original'],validate=True) if item['original'] is not None else None
        if digest(original)!=item['before']:raise ValueError('Repair backup failed verification')
        current=file_value(path)
        if digest(current) not in (item['after'],item['before']):
            raise ValueError('An installed file changed since repair; preserve it for review')
        if current!=original:changes.append((path,current,original))
    for path,current,original in changes:
        if file_value(path)!=current:raise ValueError('Installed file changed during restore')
        write_managed_file(path,original)
        if file_value(path)!=original:raise ValueError('Restore verification failed')
    return {'restored':len(changes),'backup':name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-only', action='store_true')
    parser.add_argument('--claude-hooks',action='store_true',help='Add optional Claude Code hooks (board context at start, quota checkpoints, offline at end) to ~/.claude/settings.json')
    parser.add_argument('--full-tools',action='store_true',help='Expose all MCP tools (administration included) instead of the compact worker catalog')
    parser.add_argument('--client',choices=['codex','claude','both'],default='codex')
    parser.add_argument('--repair-skill',action='append',default=[],metavar='NAME',help='Explicitly migrate a legacy Codex skill to .agents/skills with a verified backup; repeat for multiple skills')
    maintenance=parser.add_mutually_exclusive_group()
    maintenance.add_argument('--doctor',action='store_true',help='Preview skill and hook repairs and inspect MCP selection')
    maintenance.add_argument('--apply-repair',metavar='PLAN_ID',help='Apply the exact plan reported by --doctor')
    maintenance.add_argument('--restore-repair',metavar='BACKUP_ID',help='Restore files from a verified repair backup')
    parser.add_argument('--workspace',type=Path,default=ROOT,help='Workspace used to diagnose MCP selection')
    args = parser.parse_args()
    if args.repair_skill and args.client=='claude':parser.error('--repair-skill requires the Codex client')
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required.')
    clients=['codex','claude'] if args.client=='both' else [args.client]
    if args.doctor or args.apply_repair or args.restore_repair:
        profile=Path.home()
        config_root=Path(os.environ.get('CODEX_HOME',profile/'.codex'))
        backups=config_root/'backups/scrum-studio'
        if args.restore_repair:
            result=restore_repair(backups,args.restore_repair,profile)
        else:
            plan,changes=repair_plan(clients,profile,config_root,args.workspace.resolve())
            result=apply_repair(plan,changes,args.apply_repair,backups) if args.apply_repair else plan
        print(json.dumps(result,indent=2));return
    executables={client:shutil.which(client) for client in clients}
    if not args.skills_only and any(not exe for exe in executables.values()):
        raise SystemExit('Selected client CLI not found in PATH. Install it first or use --skills-only. The website can run without an agent client.')
    profile = Path.home()
    config_root = Path(os.environ.get('CODEX_HOME', profile / '.codex'))
    backup = config_root / 'backups' / 'scrum-studio' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True)
    config = config_root / 'config.toml'
    if config.exists():
        shutil.copy2(config, backup / 'config.toml')
    if 'claude' in clients and (profile/'.claude.json').exists():
        shutil.copy2(profile/'.claude.json',backup/'claude.json')
    for name in dict.fromkeys(args.repair_skill):
        result=repair_skill(name,profile,config_root,backup)
        print(f'Skill {name}: {result}')
    folders = sorted(p for p in (ROOT / 'skills').iterdir() if (p / 'SKILL.md').is_file())
    for client in clients:
        target_root=profile/('.agents' if client=='codex' else '.claude')/'skills'
        target_root.mkdir(parents=True,exist_ok=True)
        for source in folders:
            target = target_root / source.name
            if target.exists():
                shutil.copytree(target, backup / ('skills' if client=='codex' else 'claude-skills') / source.name)
            shutil.copytree(source, target, dirs_exist_ok=True)
            for file in source.rglob('*'):
                if file.is_file() and file.read_bytes() != (target / file.relative_to(source)).read_bytes():
                    raise RuntimeError(f'Skill verification failed: {source.name}')
    if not args.skills_only:
        for client in clients:
            # Re-running setup upgrades an existing registration (for example to
            # the compact worker profile); a missing entry is not an error.
            subprocess.run([executables[client],'mcp','remove','scrum-studio']+(['--scope','user'] if client=='claude' else []),check=False,capture_output=True)
            command=[executables[client],'mcp','add','scrum-studio']
            if client=='claude':
                command += ['--transport','stdio','--scope','user']
            subprocess.run(command+['--',sys.executable,str(ROOT/'mcp_server.py')]+([] if args.full_tools else ['--profile','worker']),check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_mcp.py'), '--command', sys.executable, '--server', str(ROOT / 'mcp_server.py')], check=True)
    if args.claude_hooks and 'claude' in clients:
        install_claude_hooks(profile/'.claude'/'settings.json',backup)
        print('Claude hooks installed in ~/.claude/settings.json')
    print(f'Installed {len(folders)} skills. Backup: {backup}')
    print('Reopen the selected client chat to load updated tools and skills.')
HOOK_EVENTS={'SessionStart':'session-start','UserPromptSubmit':'prompt','Stop':'stop','SessionEnd':'session-end'}

def install_claude_hooks(settings_path,backup):
    """Merge Scrum Studio hooks, replacing only previous Scrum Studio entries."""
    settings={}
    if settings_path.exists():
        shutil.copy2(settings_path,backup/'claude-settings.json')
        settings=json.loads(settings_path.read_text(encoding='utf-8') or '{}')
    settings=merged_claude_hooks(settings)
    settings_path.parent.mkdir(parents=True,exist_ok=True)
    temporary=settings_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings,indent=2),encoding='utf-8');os.replace(temporary,settings_path)


def merged_claude_hooks(settings,events=HOOK_EVENTS):
    hooks=settings.setdefault('hooks',{})
    script=ROOT/'scripts'/'claude_hooks.py'
    for event,action in HOOK_EVENTS.items():
        if event not in events:continue
        groups=[]
        for group in hooks.get(event,[]):
            remaining=[hook for hook in group.get('hooks',[]) if 'claude_hooks.py' not in hook.get('command','')]
            if remaining:groups.append({**group,'hooks':remaining})
        groups.append({'hooks':[{'type':'command','command':f'"{sys.executable}" "{script}" {action}','timeout':15}]})
        hooks[event]=groups
    return settings

if __name__ == '__main__':
    main()
