"""Machine-local MCP selection; names-only discovery, no server/model calls."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

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

def plugin_integrations(home=None):
    """Names-only local inventory; cached packages do not prove installation/auth."""
    base=Path(os.environ.get('CODEX_HOME',Path(home or Path.home())/'.codex'))
    plugins={};items={}
    try:
        import tomllib
        plugins=tomllib.loads((base/'config.toml').read_text(encoding='utf-8-sig')).get('plugins',{})
    except (OSError,ValueError,ImportError):pass
    for manifest in sorted((base/'plugins/cache').glob('*/*/*/.codex-plugin/plugin.json')):
        try:
            root=manifest.parent.parent.resolve();meta=_json(manifest)
            source=meta.get('mcpServers','./.mcp.json')
            if isinstance(source,str):
                path=(root/source).resolve()
                if not path.is_relative_to(root):continue
                servers=_json(path).get('mcpServers',{})
            elif isinstance(source,dict):servers=source
            else:continue
            if not isinstance(servers,dict):continue
            plugin=root.parent.name;marketplace=root.parent.parent.name
            setting=plugins.get(plugin+'@'+marketplace,{}) if isinstance(plugins,dict) else {}
            status=('enabled' if setting['enabled'] else 'disabled') if isinstance(setting,dict) and isinstance(setting.get('enabled'),bool) else 'cached; installation unverified'
            for name in valid_names(list(servers)):
                items[(plugin,marketplace,name)]={'name':name,'plugin':plugin,'marketplace':marketplace,'status':status}
        except (OSError,ValueError,TypeError):continue
    return list(items.values())


def listing(folder,home=None):
    saved=load(folder);result={}
    for client in CLIENTS:
        configured,warnings=discover(client,folder,home)
        choice=saved.get(client,{'mode':'inherit','servers':[]})
        names=sorted(set(configured)|set(choice['servers'])|{REQUIRED})
        result[client]={**choice,'warnings':warnings,'servers_available':[{'name':n,'required':n==REQUIRED,'available':n==REQUIRED or n in configured,'enabled':configured.get(n,{}).get('enabled',True)} for n in names]}
    result['codex']['plugin_integrations']=plugin_integrations(home)
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


def effective(client,folder,home=None):
    """Names-only diagnosis for the managed worker, without starting a client."""
    choice=load(folder).get(client,{'mode':'inherit','servers':[]})
    configured,warnings=discover(client,folder,home)
    missing=sorted(set(choice['servers'])-configured.keys()) if choice['mode']=='selected' else []
    names=set(choice['servers']) & configured.keys() if choice['mode']=='selected' else {
        name for name,entry in configured.items() if entry.get('enabled',True)}
    return {'mode':choice['mode'],'effective_specialists':sorted(names-{REQUIRED}),
            'scrum_registered':REQUIRED in configured,'missing':missing,'warnings':warnings}


def worker_readiness(client, folder, channel):
    from scripts.cli_runtime import codex_executable

    if client not in CLIENTS:
        raise ValueError('Select Codex or Claude')
    checks=[]
    try:
        executable=codex_executable() if client=='codex' else shutil.which('claude')
    except (OSError,RuntimeError):
        executable=None
    checks.append({'key':'runtime','state':'ready' if executable else 'missing',
                   'detail':client+' CLI detected locally.' if executable else client+' CLI is unavailable.',
                   **({} if executable else {'next':'Install or configure the selected CLI runtime.'})})
    try:
        tools=effective(client,folder)
        if client=='codex' and tools['mode']=='selected' and not tools['missing'] and not tools['warnings']:
            codex_options(folder)
    except (OSError,ValueError):
        tools={'effective_specialists':[]}
        checks.append({'key':'tools','state':'unknown','detail':'The worker tool configuration could not be resolved.',
                       'next':'Review the selected provider and MCP settings.'})
    else:
        state='missing' if tools['missing'] else 'unknown' if tools['warnings'] else 'ready'
        detail=('Missing selected servers: '+', '.join(tools['missing']) if tools['missing'] else
                'Some configuration files could not be read.' if tools['warnings'] else
                'The effective configured tool names were resolved.')
        checks.append({'key':'tools','state':state,'detail':detail,
                       **({} if state=='ready' else {'next':'Update MCP selection in Settings and check again.'})})
    return {'coordination':channel,'effective_specialists':tools['effective_specialists'],'checks':checks}


def graph_diagnostic(folder, paths=()):
    """Probe the local CLI on request. Do not create or repair an index."""
    if not isinstance(paths,(list,tuple)) or len(paths)>6 or any(not isinstance(path,str) or not path or len(path)>512 or
                                PurePosixPath(path).as_posix()!=path or PurePosixPath(path).is_absolute() or
                                path=='.' or '..' in PurePosixPath(path).parts or any(c in path for c in '\\:\x00')
                                for path in paths):
        raise ValueError('Use at most six repository-relative file paths')
    requested=list(dict.fromkeys(paths))
    checks=[]
    result={'checks':checks,'paths':[],
            'fallback':'Use rg and bounded source reads for unavailable or insufficient graph results.'}

    def add(key,state,detail):
        checks.append({'key':key,'state':state,'detail':detail})

    executable=shutil.which('codebase-memory-mcp')
    add('executable','ready' if executable else 'missing',
        'Codebase Memory CLI was found on PATH.' if executable else 'Install the Codebase Memory CLI or add it to PATH.')
    if not executable:return result
    folder=Path(folder).resolve()
    deadline=time.monotonic()+18

    def query(tool,*flags,payload=None):
        timeout=deadline-time.monotonic()
        if timeout<=0:raise TimeoutError
        run=subprocess.run([executable,'cli','--quiet',tool,*flags],cwd=folder,
                           input=json.dumps(payload) if payload is not None else '',
                           text=True,encoding='utf-8',errors='replace',capture_output=True,
                           timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if run.returncode:
            raise RuntimeError('The local coordination endpoint is inaccessible.' if 'endpoint' in run.stderr.lower()
                               else 'The CLI command failed. Check its installation and local permissions.')
        output=json.loads(run.stdout)
        if not isinstance(output,dict) or output.get('error'):
            raise ValueError('Invalid graph diagnostic response')
        return output

    found=None
    complete=False
    try:
        for offset in (0,50,100):
            page=query('list_projects','--format','json','--limit','50','--offset',str(offset))
            found=next((row for row in page['projects'] if Path(row['root_path']).resolve()==folder),None)
            complete=not page.get('has_more')
            if found or complete:break
    except (subprocess.TimeoutExpired,TimeoutError):
        add('endpoint','unknown','The local CLI exceeded the diagnostic time limit.')
        return result
    except RuntimeError as error:
        add('endpoint','blocked',str(error))
        return result
    except (OSError,ValueError,KeyError,TypeError):
        add('endpoint','unknown','The local CLI response could not be verified.')
        return result
    add('endpoint','ready','The local CLI replied.')
    if not found:
        add('index','missing' if complete else 'unknown',
            'No index matches this checkout. Index it explicitly if structural queries are needed.' if complete
            else 'No match was found within the bounded list of local indexes.')
        return result
    add('index','ready','A local index matches this exact checkout.')
    result['indexed_project']=found['name']
    if not requested:
        add('coverage','unknown','Supply file paths to check coverage.')
        return result
    try:
        coverage=query('check_index_coverage',payload={'project':found['name'],'paths':requested,'format':'json'})
        rows={row['requested_path']:row for row in coverage['paths']}
    except (OSError,RuntimeError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired,TimeoutError):
        add('coverage','unknown','Coverage could not be confirmed within the diagnostic limits.')
        return result
    result['paths']=[{'path':path,'status':rows.get(path,{}).get('status','unknown'),
                      'freshness':rows.get(path,{}).get('freshness','unknown')} for path in requested]
    fresh={'fresh','current','unchanged'}
    states={row['freshness'] for row in result['paths']}
    healthy=all(row['status']=='no_recorded_issue' for row in result['paths'])
    state='ready' if healthy and states<=fresh else 'partial' if not healthy else 'stale'
    add('coverage',state,
        'No issue was recorded for the requested files. Coverage is best-effort.' if state=='ready'
        else 'At least one file has incomplete, stale or unverified coverage. Read its source.')
    return result

def codex_options(folder):
    configured,names=selected('codex',folder)
    if names is None:return []
    # Codex CLI splits dotted override paths literally; quoted components create
    # a different server rather than addressing the existing TOML table.
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+',name) for name in set(configured)|{REQUIRED}):
        raise ValueError('Codex MCP selection requires server IDs containing only letters, digits, underscores or hyphens')
    return [part for name in sorted(set(configured)|{REQUIRED}) for part in ('-c','mcp_servers.'+name+'.enabled='+('true' if name in names else 'false'))]

def claude_config(folder,scrum,include_scrum=True):
    configured,names=selected('claude',folder)
    if not include_scrum and names is None:
        configured,warnings=discover('claude',folder)
        if warnings:raise ValueError('Cannot exclude Scrum: unreadable Claude MCP configuration')
        names=set(configured)
    result={REQUIRED:scrum} if include_scrum else {}
    if names is not None:result.update({name:configured[name] for name in names if name!=REQUIRED})
    return result,names is not None
