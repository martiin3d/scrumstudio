"""Install bundled skills and register the local MCP using local paths."""
import argparse
import hashlib
from datetime import datetime
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]

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
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-only', action='store_true')
    parser.add_argument('--client',choices=['codex','claude','both'],default='codex')
    parser.add_argument('--repair-skill',action='append',default=[],metavar='NAME',help='Explicitly migrate a legacy Codex skill to .agents/skills with a verified backup; repeat for multiple skills')
    args = parser.parse_args()
    if args.repair_skill and args.client=='claude':parser.error('--repair-skill requires the Codex client')
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required.')
    clients=['codex','claude'] if args.client=='both' else [args.client]
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
            command=[executables[client],'mcp','add','scrum-studio']
            if client=='claude':
                command += ['--transport','stdio','--scope','user']
            subprocess.run(command+['--',sys.executable,str(ROOT/'mcp_server.py')],check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_mcp.py'), '--command', sys.executable, '--server', str(ROOT / 'mcp_server.py')], check=True)
    print(f'Installed {len(folders)} skills. Backup: {backup}')
    print('Reopen the selected client chat to load updated tools and skills.')
if __name__ == '__main__':
    main()
