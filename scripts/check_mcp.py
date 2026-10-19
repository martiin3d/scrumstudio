"""Bounded startup probe. Reports timings and diagnostics, never project contents."""
import argparse
import json
from pathlib import Path
import math
import sys
import time
from mcp_client import JsonClient,ToolError

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command',default=sys.executable)
    parser.add_argument('--server',default=str(Path(__file__).resolve().parents[1]/'mcp_server.py'))
    parser.add_argument('--timeout',type=float,default=15)
    parser.add_argument('--project-path',type=Path,help='Check registration without opening/creating the folder')
    args=parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0<args.timeout<=60:parser.error('--timeout must be greater than zero and at most 60 seconds')
    started=time.perf_counter()
    client=None
    phase='start'
    try:
        client=JsonClient([args.command,args.server])
        phase='initialize'
        info=client.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'scrum-install-check','version':'2'}},timeout=args.timeout)['serverInfo']
        client.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        phase='tools/list'
        tools=client.rpc('tools/list',timeout=args.timeout)['tools']
        if not isinstance(tools,list):raise ValueError('Malformed tools response')
        phase='projects'
        result=client.rpc('tools/call',{'name':'projects','arguments':{}},timeout=args.timeout)
        if not isinstance(result,dict):raise ValueError('Malformed tool response')
        if result.get('isError'):raise ToolError(result)
        projects=json.loads(result['content'][0]['text'])
        if not isinstance(projects,list) or any(not isinstance(p,dict) for p in projects):raise ValueError('Malformed projects response')
        response={'handshake':'ok','server':info['name'],'version':info['version'],'tools':len(tools),
                  'projects_readable':True,'project_count':len(projects),'elapsed_ms':round((time.perf_counter()-started)*1000,2),
                  'transport':'direct stdio','writes_tested':False}
        if args.project_path is not None:
            target=args.project_path.resolve()
            response['project_registered']=any(isinstance(p.get('path'),str) and Path(p['path']).resolve()==target for p in projects)
        response['next_step']='If desktop calls are much slower, use scripts/mcp_client.py with the same interpreter/registry. A successful listing does not prove permission to write another project folder.'
        print(json.dumps(response))
        return 0
    except (RuntimeError,OSError,ValueError,KeyError,TypeError,IndexError) as error:
        response={'handshake':'failed','phase':phase,'error':str(error),'elapsed_ms':round((time.perf_counter()-started)*1000,2),'retried':False}
        if isinstance(error,ToolError) and error.detail:response['diagnostic']=error.detail
        print(json.dumps(response))
        return 1
    finally:
        if client is not None:client.close()

if __name__=='__main__':
    raise SystemExit(main())
