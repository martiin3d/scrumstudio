"""Wait for one project Chat answer in native code, without model polling."""
import argparse
import json
import time
from mcp_client import ScrumClient
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import idle_signals


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=int,required=True);parser.add_argument('--actor',type=int,required=True)
    parser.add_argument('--question',required=True);parser.add_argument('--options',default='[]',help='JSON array of concise choices')
    parser.add_argument('--question-category',choices=['missing_input','conflicting_requirements','visual_review','playtest','access'])
    parser.add_argument('--human-requirement',help='Why the agent cannot resolve this concrete question itself')
    args=parser.parse_args();rpc=None;key=None
    import os
    context=os.environ.get('SCRUM_CLI_CONTEXT')
    if not context:rpc=ScrumClient(timeout=10,channel='supervisor')
    def call(name,payload):
        if context:
            from scrum_cli import coordinated
            return coordinated(name,payload,context)
        return rpc.call(name,payload)
    try:
        payload={'project':args.project,'actor':args.actor,'action':'request','kind':'question','question':args.question,'options':json.loads(args.options)}
        if args.question_category:payload['question_category']=args.question_category
        if args.human_requirement:payload['human_requirement']=args.human_requirement
        row=call('agent_interaction',payload)
        if row.get('deferred'):
            print(json.dumps({'state':'waiting_for_human','deferred':True,'id':row['id'],'instruction':'This task is parked. Exit so the supervisor can select other ready work. Do not poll or finish this task.'}));return 0
        key=row['id'];print(json.dumps({'state':'waiting_for_human','id':key,'location':'Project Chat'}),flush=True)
        folder=call('project_settings',{'project':args.project})['workspace_root'] or Path.cwd()
        stamp=idle_signals.stamp(folder);next_check=0
        while True:
            time.sleep(2)
            current=idle_signals.stamp(folder)
            if current==stamp and time.monotonic()<next_check:continue
            stamp=current;next_check=time.monotonic()+60
            row=call('agent_interaction',{'project':args.project,'actor':args.actor,'action':'get','id':key})
            if row['state']=='answered':print(json.dumps({'state':'answered','answer':row['answer']}));return 0
            if row['state']!='pending':print(json.dumps({'state':row['state'],'answer':None}));return 1
    finally:
        if key:
            try:call('agent_interaction',{'project':args.project,'actor':args.actor,'action':'cancel','id':key})
            except RuntimeError:pass
        if rpc:rpc.close()


if __name__=='__main__':raise SystemExit(main())
