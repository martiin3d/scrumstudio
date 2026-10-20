"""Optional Claude Code hooks: board context and safe checkpoints without model turns.

session-start  stdout becomes session context: resume list, next task, quota/Stop.
prompt         adds context only when Stop/quota is active (otherwise silent).
stop           quota checkpoint at the end of each response (a safe boundary).
session-end    offline checkpoint: unfinished claims return to TODO with continuity.

Folders without a board, bridge-launched workers and any error stay silent, exit 0.
"""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
IDENTITY=os.environ.get('SCRUM_CLAUDE_IDENTITY','agent:scrum:claude-primary')
NAME=os.environ.get('SCRUM_CLAUDE_NAME','Claude primary')

def inside(child,parent):
    child=os.path.normcase(str(Path(child).resolve()));parent=os.path.normcase(str(Path(parent).resolve()))
    return child==parent or child.startswith(parent.rstrip(os.sep)+os.sep)

def board(cwd):
    from store import run
    matches=[p for p in run('projects',{}) if p.get('available') and p.get('path') and inside(cwd,p['path'])]
    if not matches:return None,None,None
    project=max(matches,key=lambda p:len(str(p['path'])))
    actor=run('user_register',{'identity':IDENTITY,'name':NAME,'kind':'agent'})['id']
    return run,project,actor

def line(task):
    text=f"{task.get('code')} {task.get('title')} [{task.get('status')}]"
    if task.get('continuity'):text+=' - '+' '.join(task['continuity'].split())[:300]
    return text

def session_start(run,project,actor):
    started=run('work_start',{'project':project['id'],'actor':actor,'client':'claude'})
    check=started.get('checkpoint',{})
    out=[f"Scrum Studio board (loaded by hook; no discovery call needed): project {project['id']} \"{project.get('name','')}\", your actor {actor}."]
    out.append('Quota/Stop: '+('STOP - '+check.get('reason','policy')+'. Do not start board work.' if check.get('should_stop') else 'ok'))
    for task in started.get('resume',[]):out.append('Resume first (own unfinished): '+line(task))
    following=(started.get('next') or {}).get('task')
    if following:out.append('Next eligible: '+line(following))
    locks=started.get('own_file_locks',{}).get('items',[])
    if locks:out.append(f'Own file locks to review: {len(locks)}')
    out.append(f'To work on a task: work_start(project={project["id"]}, actor={actor}, client="claude", id=TASK, claim=true) starts and claims it in one call. Finish with work_finish (claim_next=true to chain the next task).')
    print('\n'.join(out))

def prompt(run,project,actor):
    check=run('agent_checkpoint',{'project':project['id'],'actor':actor,'client':'claude'})
    if check.get('should_stop'):
        print('Scrum Studio: STOP - '+check.get('reason','policy')+'. Own claimed work was handed off with continuity; do not edit board tasks until it clears.')

def stop(run,project,actor):
    run('agent_checkpoint',{'project':project['id'],'actor':actor,'client':'claude'})

def session_end(run,project,actor):
    run('agent_checkpoint',{'project':project['id'],'actor':actor,'client':'claude','action':'offline','summary':'Claude session ended; resume from task continuity.'})

ACTIONS={'session-start':session_start,'prompt':prompt,'stop':stop,'session-end':session_end}

def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ACTIONS or os.environ.get('SCRUM_BRIDGE_WORKER'):return 0
    try:
        try:payload=json.loads(sys.stdin.read(200000) or '{}')
        except ValueError:payload={}
        run,project,actor=board(payload.get('cwd') or os.getcwd())
        if project:ACTIONS[argv[0]](run,project,actor)
    except Exception:
        pass # Hooks never block or break the Claude session.
    return 0

if __name__=='__main__':
    sys.exit(main())
