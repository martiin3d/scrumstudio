"""Safe projections of execution events for shared task history."""
import re
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar


_actions = ContextVar('git_history_actions', default=None)
_verbs = ('--version|add|branch|check-ignore|checkout|cherry-pick|commit|commit-tree|'
          'config|diff|fetch|hash-object|ls-files|ls-remote|ls-tree|merge|merge-base|'
          'merge-tree|pull|push|rebase|remote|reset|rev-parse|show|status|switch|'
          'update-index|update-ref|worktree')
_command = re.compile(r'(?im)(?:^|[;&|\n]|-Command ["\x27])\s*(?:&\s*)?git(?:\.exe)?\s+'
                      r'(?:(?:-C|-c)\s+(?:"[^"]*"|\x27[^\x27]*\x27|[^\s;&|"\x27])+\s+)*('
                      + _verbs + r')(?=\s|[;&|]|$)')
_sha = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
_ref = re.compile(r'[A-Za-z0-9_./-]{1,200}')


def error_summary(raw):
    """Keep diagnostic categories, never arbitrary stderr or shell arguments."""
    for pattern, label in (
        (r'failed to connect|could not connect|connection refused', 'connection failed'),
        (r'could not resolve (?:host|proxy)', 'DNS resolution failed'),
        (r'timed out|timeout', 'timed out'),
        (r'non-fast-forward|fetch first', 'non-fast-forward rejected'),
        (r'authentication|credentials|access denied|permission denied', 'access denied'),
        (r'conflict', 'merge conflict'),
        (r'publication .*failed|push failed', 'publication failed'),
        (r'checkout', 'checkout recovery required'),
    ):
        if re.search(pattern, raw or '', re.I):
            return label
    return 'see the local Git diagnostic'


def refs(source, target):
    source, target = (value if _ref.fullmatch(value or '') else 'Unknown' for value in (source, target))
    return f'Source {source} → target {target}.'


@contextmanager
def capture():
    actions = []
    token = _actions.set(actions)
    try:
        yield actions
    finally:
        _actions.reset(token)


def native(args, result):
    actions = _actions.get()
    if actions is None:
        return
    verb = args[0] if args and re.fullmatch(_verbs, args[0]) else 'operation'
    if isinstance(result, subprocess.TimeoutExpired):
        outcome = 'timed out'
    elif isinstance(result, Exception):
        outcome = 'could not run'
    elif result.returncode:
        outcome = f'exit {result.returncode}, {error_summary(result.stderr)}'
    else:
        outcome = 'succeeded'
    actions.append(f'git {verb} · {outcome} · Native publisher · Sandbox: Unknown')


def remote_sha(result, ref):
    if result.returncode:
        return ''
    for line in result.stdout.splitlines():
        value, _, observed = line.partition('\t')
        if observed == ref and _sha.fullmatch(value):
            return value
    return ''


def append_history(activity, db, task, actor, heading, actions):
    # Keep pages readable without truncating an attempt.
    for offset in range(0, max(1, len(actions)), 8):
        activity(db, task, actor, 'Git history', '\n'.join([heading, *actions[offset:offset+8]]))


def worker(item, profile, approved):
    verbs = _command.findall(item.get('command', ''))
    if not verbs:
        return []
    status = item.get('status')
    exit_code = item.get('exitCode')
    if status == 'declined' or item.get('source')=='userShell':
        context, sandbox = 'Unknown', 'Unknown'
    elif approved and len(verbs)==1:
        context, sandbox = 'Approved execution outside the sandbox', 'inactive'
    elif approved:
        context = sandbox = 'Unknown'
    elif profile == ':danger-full-access':
        context, sandbox = 'Full access worker', 'inactive'
    elif profile == 'scrum-worker':
        context, sandbox = 'Sandboxed worker', 'active'
    else:
        context = sandbox = 'Unknown'
    if len(verbs) > 1:
        outcome = 'individual result Unknown'
    else:
        outcome = status if status in ('completed', 'failed', 'declined', 'inProgress') else 'result Unknown'
    if type(exit_code) is int:
        outcome += f' (shell exit {exit_code})'
        if exit_code:
            outcome += ', ' + error_summary(item.get('aggregatedOutput'))
    return [f'git {verb} · {outcome} · {context} · Sandbox: {sandbox}' for verb in verbs]


def save_worker(project, actor, task, actions):
    import git_sync
    import store
    return store.run('git_history',dict(project=project,actor=actor,id=task,worker=True,
                     heading='Agent-assisted Git recovery',actions=actions,capability=git_sync.FINALIZE_TOKEN))


class ClaudeRecovery:
    """Correlate Claude's Bash and PowerShell uses with their tool results."""
    def __init__(self, project, actor, task):
        self.project, self.actor, self.task = project, actor, task
        self.pending = {}

    def __call__(self, event):
        message = event.get('message')
        if not isinstance(message,dict) or not isinstance(message.get('content'),list):
            return
        for block in message['content']:
            if not isinstance(block,dict):
                continue
            if block.get('type')=='tool_use' and block.get('name') in ('Bash','PowerShell'):
                command=(block.get('input') or {}).get('command','')
                if _command.search(command):
                    self.pending[block['id']]=command
            if block.get('type')=='tool_result':
                command=self.pending.pop(block.get('tool_use_id'),None)
                if command:
                    status='failed' if block.get('is_error') else 'completed'
                    actions=worker(dict(command=command,status=status),None,False)
                    save_worker(self.project,self.actor,self.task,actions)
