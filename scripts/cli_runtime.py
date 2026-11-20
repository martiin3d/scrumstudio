"""Resolve installed provider tooling without downloading or changing a model."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys


def worker_python():
    """Prefer the installed desktop runtime for sandboxed shell helpers."""
    if sys.platform=='win32':
        bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if bundled.is_file():return str(bundled.resolve())
    return sys.executable


def model_name(value):
    if not isinstance(value,str) or (value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,255}',value)):
        raise ValueError('Model must be blank for provider default, or an explicit model ID/alias (max256, no whitespace or command options)')
    return value


def codex_executable():
    explicit=os.environ.get('SCRUM_CODEX_EXECUTABLE')
    if explicit:
        path=Path(explicit).expanduser()
        if path.is_file():
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
    executable = shutil.which('codex')
    if executable or os.name != 'nt':
        return executable

    # WindowsApps is normally inaccessible to directory enumeration.
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run(
            [str(powershell), '-NoProfile', '-NonInteractive', '-Command',
             'Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty InstallLocation'],
            capture_output=True, text=True, check=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for location in result.stdout.splitlines():
        if location.strip():
            packaged = Path(location.strip()) / 'app/resources/codex.exe'
            if packaged.is_file():
                return str(packaged)
    return None
