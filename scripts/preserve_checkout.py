"""Privately preserve exact claimed recovery edits without deleting or publishing."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import scrum_cli as cli
import git_sync as git
import git_retained


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context',default=os.environ.get('SCRUM_CLI_CONTEXT'))
    p.add_argument('--target',required=True,help='Existing local integrated branch; never inferred from dirty HEAD')
    a=p.parse_args()
    if not a.context:p.error('A private claimed CLI context is required')
    state=cli.context_file(a.context);current=state.get('current') or {}
    if not current:raise ValueError('A live recovery claim is required')
    task=cli.coordinated('task_get',{'id':current['id'],'full':True},a.context)
    if task['category']!='Git recovery' or task['status']!='In Progress' or task['owner_id']!=state['actor'] or not task['locked']:
        raise ValueError('Only your live Git recovery task may use this preservation route')
    policy=cli.coordinated('project_settings',{},a.context)
    if policy['git_mode']!='Branches':raise ValueError('This helper supports canonical Branches mode only')
    status=cli.coordinated('agent_status',{},a.context)
    if set(status['active_task_ids'])!={task['id']}:raise ValueError('Finish other live claims before preserving a shared checkout')
    # Server validates the private token, quotas and exact planned file ownership.
    cli.coordinated('file_locks',{'id':task['id'],'action':'acquire','paths':task['files']},a.context)
    with git.checkout_lock(policy['workspace_root']):
        result=git_retained.preserve(policy['workspace_root'],task['files'],task['code'],a.target)
    print(json.dumps(result,separators=(',',':')))


if __name__=='__main__':main()
