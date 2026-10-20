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
    args=parser.parse_args();rpc=ScrumClient(timeout=10);key=None
    try:
        row=rpc.call('agent_interaction',{'project':args.project,'actor':args.actor,'action':'request','kind':'question','question':args.question,'options':json.loads(args.options)})
        key=row['id'];print(json.dumps({'state':'waiting_for_human','id':key,'location':'Project Chat'}),flush=True)
        folder=rpc.call('project_settings',{'project':args.project})['workspace_root'] or Path.cwd()
        stamp=idle_signals.stamp(folder);next_check=0
        while True:
            time.sleep(2)
            current=idle_signals.stamp(folder)
            if current==stamp and time.monotonic()<next_check:continue
            stamp=current;next_check=time.monotonic()+60
            row=rpc.call('agent_interaction',{'project':args.project,'actor':args.actor,'action':'get','id':key})
            if row['state']=='answered':print(json.dumps({'state':'answered','answer':row['answer']}));return 0
            if row['state']!='pending':print(json.dumps({'state':row['state'],'answer':None}));return 1
    finally:
        if key:
            try:rpc.call('agent_interaction',{'project':args.project,'actor':args.actor,'action':'cancel','id':key})
            except RuntimeError:pass
        rpc.close()


if __name__=='__main__':raise SystemExit(main())
