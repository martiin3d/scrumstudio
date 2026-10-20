"""Machine-local MCP selection; names-only discovery, no server/model calls."""
import json
import os
import re
import tempfile
from pathlib import Path

CLIENTS=('codex','claude')
REQUIRED='scrum-studio'

def valid_names(names):
    if not isinstance(names,list) or len(names)>100 or any(not isinstance(n,str) or not n or len(n)>128 or any(ord(c)<32 for c in n) for n in names):
        raise ValueError('Use at most 100 valid server names')
    return sorted(set(names)-{REQUIRED})

def load(folder):
    path=Path(folder)/'.scrum/local/mcp-selection.json'
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:return {}
    except (OSError,ValueError):raise ValueError('Cannot read local MCP selection; repair the local settings file') from None
    if not isinstance(data,dict):raise ValueError('Invalid local MCP selection')
    for client,item in data.items():
        if client not in CLIENTS or not isinstance(item,dict) or item.get('mode') not in ('inherit','selected'):raise ValueError('Invalid local MCP selection')
        valid_names(item.get('servers',[]))
    return data

def _json(path):
    if not path.exists():return {}
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('Invalid configuration')
    return value

def discover(client,folder,home=None):
    if client not in CLIENTS:raise ValueError('Unknown MCP provider')
    home=Path(home) if home else Path.home();folder=Path(folder).resolve()
    servers={};warnings=[]
    def merge(path,parser,extract=lambda d:d.get('mcpServers',{})):
        try:
            if not path.exists():return
            value=extract(parser(path))
            if not isinstance(value,dict):raise ValueError('Invalid server configuration')
            for name,config in value.items():
                valid_names([name])
                if not isinstance(config,dict):raise ValueError('Invalid server configuration')
                servers[name]=config
        except (OSError,ValueError,ImportError):
            warnings.append('Could not read '+path.name+' configuration; no credentials or server details are exposed')
    if client=='codex':
        def toml(path):
            try:import tomllib
            except ImportError:raise ValueError('TOML discovery needs Python 3.11 or newer') from None
            return tomllib.loads(path.read_text(encoding='utf-8-sig'))
        base=Path(os.environ.get('CODEX_HOME',home/'.codex'))
        merge(base/'config.toml',toml,lambda d:d.get('mcp_servers',{}))
        for parent in [*reversed(folder.parents),folder]:
            path=parent/'.codex/config.toml'
            if path!=base/'config.toml':merge(path,toml,lambda d:d.get('mcp_servers',{}))
    else:
        merge(home/'.claude.json',_json)
        merge(folder/'.mcp.json',_json)
        def local(d):
            projects=d.get('projects',{})
            if not isinstance(projects,dict):raise ValueError('Invalid project configuration')
            for path,config in projects.items():
                if os.path.normcase(str(Path(path).resolve()))==os.path.normcase(str(folder)):
                    return config.get('mcpServers',{})
            return {}
        merge(home/'.claude.json',_json,local)
    return servers,warnings

def listing(folder,home=None):
    saved=load(folder);result={}
    for client in CLIENTS:
        configured,warnings=discover(client,folder,home)
        choice=saved.get(client,{'mode':'inherit','servers':[]})
        names=sorted(set(configured)|set(choice['servers'])|{REQUIRED})
        result[client]={**choice,'warnings':warnings,'servers_available':[{'name':n,'required':n==REQUIRED,'available':n==REQUIRED or n in configured,'enabled':configured.get(n,{}).get('enabled',True)} for n in names]}
    return result

def save(folder,client,mode,servers):
    if client not in CLIENTS or mode not in ('inherit','selected'):raise ValueError('Invalid MCP selection')
    names=valid_names(servers);local=Path(folder)/'.scrum/local';local.mkdir(parents=True,exist_ok=True)
    lock=local/'mcp-selection.lock'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('MCP settings are being saved; retry shortly') from None
    try:
        os.close(fd);data=load(folder);data[client]={'mode':mode,'servers':names}
        fd,tmp=tempfile.mkstemp(prefix='mcp-selection-',suffix='.tmp',dir=local)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(data,stream)
            os.replace(tmp,local/'mcp-selection.json')
        finally:Path(tmp).unlink(missing_ok=True)
    finally:lock.unlink(missing_ok=True)

def selected(client,folder):
    choice=load(folder).get(client,{'mode':'inherit','servers':[]})
    if choice['mode']=='inherit':return None,None
    configured,warnings=discover(client,folder)
    if warnings:raise ValueError('Cannot apply MCP selection because a client configuration is unreadable')
    missing=set(choice['servers'])-configured.keys()
    if missing:raise ValueError('Selected MCP servers are unavailable: '+', '.join(sorted(missing)))
    return configured,set(choice['servers'])|{REQUIRED}

def codex_options(folder):
    configured,names=selected('codex',folder)
    if names is None:return []
    # Codex CLI splits dotted override paths literally; quoted components create
    # a different server rather than addressing the existing TOML table.
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+',name) for name in set(configured)|{REQUIRED}):
        raise ValueError('Codex MCP selection requires server IDs containing only letters, digits, underscores or hyphens')
    return [part for name in sorted(set(configured)|{REQUIRED}) for part in ('-c','mcp_servers.'+name+'.enabled='+('true' if name in names else 'false'))]

def claude_config(folder,scrum):
    configured,names=selected('claude',folder)
    result={REQUIRED:scrum}
    if names is not None:result.update({name:configured[name] for name in names if name!=REQUIRED})
    return result,names is not None
