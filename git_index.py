"""Repair false dirty index metadata without staging any content changes."""
import os
from pathlib import Path
import tempfile

import git_sync as git


def refresh_identical(root):
    """Caller holds checkout_lock. An existing Git index lock always wins.

    Stage into a private copy, then install it only if the entire blob/mode/stage
    listing is identical. Real edits, deletions, conflicts and staging survive.
    No working file is written, reset, stashed or checked out.
    """
    located=git._git(root,'rev-parse','--git-path','index')
    if located.returncode:return False
    index=Path(located.stdout.strip())
    if not index.is_absolute():index=Path(root)/index
    lock=index.with_name(index.name+'.lock');temporary=None
    try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:return False
    owns_lock=True
    try:
        with os.fdopen(fd,'wb') as output:
            original=index.read_bytes()
            if git._git(root,'diff','--cached','--quiet').returncode!=0:return False
            before=git._git(root,'ls-files','--stage','-z',timeout=15)
            flags=git._git(root,'ls-files','-v','-z',timeout=15)
            changed=git._git(root,'diff-files','--name-only','-z',timeout=15)
            if before.returncode or flags.returncode or changed.returncode:return False
            paths=list(filter(None,changed.stdout.split('\0')))
            if not paths or len(paths)>1000:return False
            # Keep split-index shared files relative to the original Git directory.
            handle,name=tempfile.mkstemp(prefix='scrum-index-',dir=index.parent)
            temporary=Path(name)
            with os.fdopen(handle,'wb') as stream:stream.write(original)
            for chunk in git.path_chunks(paths):
                staged=git._git(root,'add','--',*chunk,index_file=temporary,timeout=30)
                if staged.returncode:return False
            after=git._git(root,'ls-files','--stage','-z',index_file=temporary,timeout=15)
            after_flags=git._git(root,'ls-files','-v','-z',index_file=temporary,timeout=15)
            if after.returncode or after.stdout!=before.stdout or after_flags.returncode or after_flags.stdout!=flags.stdout:return False
            if index.read_bytes()!=original:return False
            output.write(temporary.read_bytes());output.flush();os.fsync(output.fileno())
        os.replace(lock,index)
        owns_lock=False
        return True
    finally:
        if owns_lock:lock.unlink(missing_ok=True)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            temporary.with_name(temporary.name+'.lock').unlink(missing_ok=True)
