"""Best-effort local control notification; checkpoints remain authoritative."""
import os
import time
from pathlib import Path

def stamp(folder):
    path=Path(folder)/'.scrum/local/control.signal'
    try:
        value=path.stat();return (value.st_mtime_ns,value.st_size)
    except OSError:return None

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
