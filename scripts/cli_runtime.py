"""Resolve installed provider tooling without downloading or changing a model."""
import os
import re
from pathlib import Path
import shutil
import sys


def model_name(value):
    if not isinstance(value,str) or (value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,255}',value)):
        raise ValueError('Model must be blank for provider default, or an explicit model ID/alias (max256, no whitespace or command options)')
    return value


def codex_executable():
    explicit=os.environ.get('SCRUM_CODEX_EXECUTABLE')
    if explicit:
        path=Path(explicit).expanduser()
        if not path.is_file():raise RuntimeError('SCRUM_CODEX_EXECUTABLE must point to an existing Codex executable')
        return str(path.resolve())
    candidates=[]
    if os.name=='nt':
        roots={Path.home()/'AppData'/'Local'}
        if os.environ.get('LOCALAPPDATA'):roots.add(Path(os.environ['LOCALAPPDATA']))
        candidates=[p for root in roots for p in (root/'OpenAI'/'Codex'/'bin').glob('*/codex.exe') if p.is_file()]
    elif sys.platform=='darwin':
        candidates=[p for p in [Path('/Applications/Codex.app/Contents/Resources/codex'),Path.home()/'Applications/Codex.app/Contents/Resources/codex'] if p.is_file()]
    if candidates:
        return str(max(candidates,key=lambda path:path.stat().st_mtime).resolve())
    return shutil.which('codex')
