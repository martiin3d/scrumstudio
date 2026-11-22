"""Derive worker aliases from a host source kept outside project storage."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import uuid


def _host_identifier():
    if os.name == 'nt':
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return uuid.UUID(winreg.QueryValueEx(key, 'MachineGuid')[0]).hex
    for path in (Path('/etc/machine-id'), Path('/var/lib/dbus/machine-id')):
        try:
            return uuid.UUID(path.read_text(encoding='ascii').strip()).hex
        except (OSError, ValueError):
            continue

    # Platforms without a system ID use an installation ID in local user state.
    from storage import atomic_json, read_json

    path = Path.home() / '.scrum-studio' / 'machine-id.json'
    if not path.exists():
        atomic_json(path, uuid.uuid4().hex, exclusive=True)
    return uuid.UUID(read_json(path)).hex


@lru_cache(maxsize=1)
def machine_scope():
    return hashlib.sha256(('scrum-studio:machine:' + _host_identifier()).encode()).hexdigest()


def worker_registration(role, key, name):
    # The portable alias identifies a worker without publishing a host-wide ID.
    payload = json.dumps([machine_scope(), role, key], separators=(',', ':'))
    alias = hashlib.sha256(payload.encode()).hexdigest()
    return {
        'identity': f'agent:machine:{role}:{alias}',
        'name': f'{name.strip()[:101]} [{alias[:16]}]',
        'kind': 'agent',
    }


def standalone_worker(client):
    if client == 'claude':
        key = os.environ.get('SCRUM_CLAUDE_IDENTITY', 'agent:scrum:claude-primary')
        name = os.environ.get('SCRUM_CLAUDE_NAME', 'Claude primary')
    else:
        key, name = 'agent:scrum:codex-primary', 'Codex primary'
    return worker_registration(client, key, name)


def bridge_worker(checkout, client, worker):
    return worker_registration('bridge', [checkout, client, worker], f'{client} · {worker}')
