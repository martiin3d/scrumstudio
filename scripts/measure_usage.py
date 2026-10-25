"""Human-requested baseline before Wakeup, or finish/read its native local report."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mcp_client import ScrumClient
from provider_usage import codex_usage
import usage_measurement


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','finish','get'])
    parser.add_argument('--project',type=int,required=True)
    parser.add_argument('--actor',type=int)
    parser.add_argument('--wakeup',action='store_true')
    args=parser.parse_args();c=ScrumClient(timeout=25)
    try:
        project=next(p for p in c.call('projects',{}) if p['id']==args.project)
        root=project['path']
        if args.action=='start':
            if args.wakeup and args.actor is None:parser.error('--wakeup requires the authorized human --actor')
            state=c.call('agent_status',{'project':args.project})
            if state.get('active_tasks') or state.get('wakeup',{}).get('running_bridges'):
                raise ValueError('Work is already active; a baseline taken now would not be before launch')
            quota=codex_usage()
            audit=c.call('coordination_metrics',{'project':args.project,'include_audit':True,'include_logs':False})['audit']
            result=usage_measurement.begin(root,args.project,quota,audit)
            if args.wakeup:
                wake=c.call('project_control',{'project':args.project,'actor':args.actor,'action':'wakeup','reason':'Human requested continuation after community workflow improvements; native before/after measurement saved.'})
                result['wakeup']=wake
        elif args.action=='finish':result=usage_measurement.finish(root,'Human-requested end of measured interval',c,args.project)
        else:result=usage_measurement.summary(root)
        print(json.dumps(result,ensure_ascii=False))
    finally:c.close()


if __name__=='__main__':main()
