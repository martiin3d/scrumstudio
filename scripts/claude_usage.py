"""Optional Claude status-line adapter. Preserves only numeric usage, no transcript."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from provider_usage import claude_payload

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='Absolute local usage JSON file read by the bridge')
    args=parser.parse_args()
    path=Path(args.output)
    if not path.is_absolute():
        raise SystemExit('--output must be absolute; keep it in .scrum/local')
    data=claude_payload(json.loads(sys.stdin.read(100000)))
    if not data:
        print('Scrum usage unavailable');return
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.usage-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as file:
            json.dump(data,file)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    print(' | '.join(f'{label}: {data[key]:g}%' for label,key in [('5h','session_used_percent'),('Week','weekly_used_percent')] if key in data))

if __name__=='__main__':
    main()
