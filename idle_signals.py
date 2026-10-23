"""Best-effort local control notification; checkpoints remain authoritative."""
import os
import time
from pathlib import Path

def stamp(folder):
    path=Path(folder)/'.scrum/local/control.signal'
    try:
        value=path.stat();return (value.st_mtime_ns,value.st_size)
    except OSError:return None

def checkout_stamp(folder, paths=()):
    """Bounded file stats, only while a checkout refusal is pending. No Git/MCP."""
    root=Path(folder).resolve();metadata=root/'.git'
    try:
        if metadata.is_file():
            value=metadata.read_text(encoding='utf-8').strip()
            if not value.startswith('gitdir:'):return None
            metadata=(root/value[7:].strip()).resolve()
        common=metadata
        if (metadata/'commondir').is_file():common=(metadata/(metadata/'commondir').read_text().strip()).resolve()
        watched=[metadata/'HEAD',metadata/'index',common/'packed-refs']
        head=(metadata/'HEAD').read_text().strip()
        if head.startswith('ref: refs/') and '..' not in head:watched.append(common/head[5:])
        for name in paths[:100]:
            path=(root/name).resolve()
            if path.is_relative_to(root) and not path.is_relative_to(root/'.git') and not path.is_relative_to(root/'.scrum'):watched.append(path)
        result=[]
        for path in watched:
            try:
                stat=path.stat();result.append((str(path),stat.st_mtime_ns,stat.st_size))
            except OSError:result.append((str(path),None,None))
        return tuple(result)
    except (OSError,ValueError):return None

def notify(action,args):
    # Legacy single-file callers/tests must not notify an unrelated folder registry.
    if 'SCRUM_DB' in os.environ and 'SCRUM_REGISTRY' not in os.environ:return
    if action not in ('project_control','project_settings','agent_manage','user_manage','git_recovery','agent_interaction'):return
    if action=='agent_interaction' and args.get('action') not in ('answer','cancel'):return
    if action=='agent_manage' and args.get('action','list') in ('list','get'):return
    if action=='project_settings' and not set(args)-{'project','actor','measurement_session'}:return
    try:
        import storage
        if args.get('project') is not None:folders=[storage.entry(args['project'])[1]]
        else:
            with storage.registry() as db:folders=[r['path'] for r in db.execute('SELECT path FROM folders')]
        for folder in folders:
            path=Path(folder)/'.scrum/local/control.signal'
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(str(time.time_ns()),encoding='ascii')
    except (OSError,ValueError,storage.sqlite3.Error):
        pass # Never report a committed control action as failed.

def checkpoint_due(at,last,running,previous,changed):
    return last is None or changed or running!=previous or at-last>=(10 if running else 60)
