"""Browser-only conditional page data; no model context or MCP tool."""
import hashlib
import json
import time
from pathlib import Path
import storage

PAGES=('Board','Agents','Notes','Users','File locks','Metrics','AI workflow')

def revision(project,page):
    _,folder=storage.entry(project)
    local=folder/'.scrum/local'
    files=[local/name for name in ('scrum.db','scrum.db-wal','locks.db','locks.db-wal','agent-profiles.db','agent-profiles.db-wal','overhead.db','overhead.db-wal')]
    files.extend([storage.registry_path().parent/'identities.db',storage.registry_path().parent/'identities.db-wal'])
    # Expired leases/telemetry change the displayed truth even without writes.
    value=[project,page,int(time.time()//60)]
    for path in files:
        try:s=path.stat();value.append((str(path),s.st_mtime_ns,s.st_size))
        except FileNotFoundError:value.append((str(path),None))
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()[:24]

def refresh(args,call):
    project=args.get('project');page=args.get('page','Board')
    if type(project) is not int or page not in PAGES:raise ValueError('Invalid web project/page')
    scope=json.dumps([args.get('days',30),args.get('filter_session'),args.get('filter_period')],sort_keys=True)
    before=revision(project,page)+hashlib.sha256(scope.encode()).hexdigest()[:8]
    if args.get('version_seen')==before:return {'changed':False,'version':before}
    if page=='Agents':
        # Agent views need no task descriptions, evidence or note bodies.
        data={'runtime':call('agent_status',{'project':project}),'managedAgents':call('agent_manage',{'project':project,'action':'list'}),'users':call('users',{})}
    else:
        data=call('snapshot',{'project':project})
        if page=='Board':
            import context_batches
            data['taskGroups']=context_batches.browser_groups(project,[t['id'] for t in data['tasks']])
        if page=='File locks':data['fileLocks']=call('file_locks',{'project':project})
        if page=='Metrics':
            selected={'filter_period':args['filter_period']} if args.get('filter_period') else {}
            data['metrics']=call('project_metrics',{'project':project,'days':args.get('days',30),'include_series':True,**selected})
            data['coordination']=call('coordination_metrics',{'project':project,'include_periods':True,**({'filter_session':args['filter_session']} if args.get('filter_session') else {}),**selected,'start_at':data['metrics']['start_at'],'end_at':data['metrics']['end_at']})
    # Never mark a concurrent mutation as seen with older data.
    after=revision(project,page)+hashlib.sha256(scope.encode()).hexdigest()[:8]
    return {'changed':True,'version':before if before==after else '', 'data':data}
