"""Refresh a local quota feed without launching a model or worker."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from overhead import usage_path
from storage import atomic_json
from mcp_client import ScrumClient
from provider_usage import codex_usage, usage_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=int, required=True)
    parser.add_argument('--actor', type=int, required=True)
    parser.add_argument('--client', choices=['codex','claude'], required=True)
    parser.add_argument('--usage-file', help='Existing provider feed; required for Claude')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.client == 'claude' and not args.usage_file:
        parser.error('Claude requires --usage-file; this monitor never invokes Claude')
    rpc = ScrumClient()
    try:
        users = rpc.call('users', {})
        if not any(u['id'] == args.actor and u['kind'] == 'agent' and u.get('active', 1) for u in users):
            raise ValueError('Select an existing active agent identity')
        target = usage_path(args.project, args.actor, args.client)
        while True:
            policy = rpc.call('project_settings', {'project': args.project})
            observed = usage_file(args.usage_file) if args.usage_file else codex_usage()
            # Empty result expires the cache instead of inventing zero usage.
            atomic_json(target, observed)
            if args.once:
                print(json.dumps({'updated': bool(observed), 'path': str(target)}))
                break
            time.sleep(policy['usage_check_seconds'])
    except KeyboardInterrupt:
        pass
    finally:
        rpc.close()


if __name__ == '__main__':
    main()
