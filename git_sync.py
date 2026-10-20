"""Automatic git branch/commit/push per task or context group, run by the server.

Policy lives in project settings (git_mode, git_commit, git_push, git_base_branch,
git_branch_prefix, git_remote). Everything runs in local code at claim and Done
time, never through a model turn. Git problems never block coordination: a claim
on another branch's dirty checkout is refused with a reason, and commit/push
errors are recorded on the task (activity + response) while Done stands.
"""
import json
import os
import re
import subprocess
import time
import unicodedata

GIT_DEFAULTS = {'git_mode': 'Off', 'git_commit': 'Task', 'git_push': 'Group', 'git_base_branch': '', 'git_branch_prefix': 'scrum/', 'git_remote': 'origin'}
PENDING = []  # pushes to run after the owning transaction commits

def validate(changed):
    if 'git_mode' in changed and changed['git_mode'] not in ('Off', 'Branches'):
        raise ValueError('git_mode must be Off or Branches')
    if 'git_commit' in changed and changed['git_commit'] not in ('Task', 'Group'):
        raise ValueError('git_commit must be Task or Group')
    if 'git_push' in changed and changed['git_push'] not in ('Group', 'Task', 'Never'):
        raise ValueError('git_push must be Group, Task or Never')
    for key, limit in (('git_base_branch', 200), ('git_branch_prefix', 60), ('git_remote', 80)):
        value = changed.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > limit or any(c.isspace() for c in value) or '..' in value or value.startswith('-'):
            raise ValueError(key + ' must be a single git-safe token without spaces')
    if changed.get('git_remote') == '':
        raise ValueError('git_remote cannot be empty')

def _git(root, *args, timeout=3):
    flags = 0x08000000 if os.name == 'nt' else 0
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=flags)

def slug(text, limit=48):
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text[:limit].rstrip('-') or 'task'

def branch_for(settings, task):
    """scrum/<group> for a context group, scrum/<CODE>-<title> otherwise."""
    prefix = settings.get('git_branch_prefix', 'scrum/')
    group = (task.get('context_group') or '').strip()
    return prefix + (slug(group) if group else task['code'].lower() + '-' + slug(task['title'], 40))

def enabled(settings):
    return settings.get('git_mode', 'Off') == 'Branches'

def state(root):
    """(branch, dirty paths outside .scrum) or None when git is unusable."""
    try:
        head = _git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
        if head.returncode:
            return None
        status = _git(root, 'status', '--porcelain', '--untracked-files=all', '--', '.', ':!.scrum')
        if status.returncode:
            return None
        dirty = [line[3:].strip().strip('"') for line in status.stdout.splitlines() if not line[3:].strip().strip('"').startswith('.scrum/')]
        return head.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return None

def on_claim(root, settings, task):
    """Move the checkout to the task's branch. Returns {'branch','switched'|'created'} or {'busy':...}/{'error':...}."""
    if not enabled(settings) or not root:
        return None
    target = branch_for(settings, task)
    current = state(root)
    if current is None:
        return {'branch': target, 'error': 'git unavailable or not a repository; working on the checkout as is'}
    branch, dirty = current
    if branch == target:
        return {'branch': target}
    if dirty:
        return {'busy': {'current_branch': branch, 'wanted': target, 'dirty': dirty[:20], 'dirty_total': len(dirty)}}
    try:
        exists = _git(root, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + target).returncode == 0
        if exists:
            result = _git(root, 'checkout', '--quiet', target, timeout=20)
            outcome = 'switched'
        else:
            base = settings.get('git_base_branch') or 'HEAD'
            if base != 'HEAD' and _git(root, 'rev-parse', '--verify', '--quiet', base).returncode:
                return {'branch': target, 'error': f'base branch {base} not found; staying on {branch}'}
            result = _git(root, 'checkout', '--quiet', '-b', target, base, timeout=20)
            outcome = 'created'
        if result.returncode:
            return {'branch': target, 'error': (result.stderr or result.stdout).strip()[:300] or 'checkout failed'}
        return {'branch': target, outcome: True, 'previous': branch}
    except (OSError, subprocess.SubprocessError) as error:
        return {'branch': target, 'error': str(error)[:300]}

def _commit(root, settings, task, message):
    files = task.get('files') or []
    if files:
        # Deleted planned files that git never tracked would fail the pathspec.
        pathspec = [f for f in files if os.path.exists(os.path.join(root, f)) or _git(root, 'ls-files', '--error-unmatch', '--', f).returncode == 0]
        if not pathspec:
            return {'committed': False, 'reason': 'no planned file exists in the checkout'}
    else:
        pathspec = ['.']
    add = _git(root, 'add', '-A', '--', *pathspec, timeout=20)
    if add.returncode:
        return {'error': 'git add: ' + (add.stderr or add.stdout).strip()[:300]}
    if not files:
        _git(root, 'reset', '-q', '--', '.scrum')  # board metadata is never task work
    if _git(root, 'diff', '--cached', '--quiet').returncode == 0:
        return {'committed': False, 'reason': 'nothing to commit'}
    commit = _git(root, 'commit', '--quiet', '-m', message, timeout=30)
    if commit.returncode:
        return {'error': 'git commit: ' + (commit.stderr or commit.stdout).strip()[:300]}
    sha = _git(root, 'rev-parse', '--short', 'HEAD').stdout.strip()
    stat = _git(root, 'diff', '--shortstat', 'HEAD~1', 'HEAD').stdout.strip()
    return {'committed': True, 'sha': sha, 'summary': stat}

def on_done(db, root, settings, task, project, actor, group_done, group_members):
    """Commit at Done according to policy and queue a push when the unit is complete.

    task: dict with id, code, title, files, context_group. group_done: every task of
    the context group is Done/Cancelled (True when the task has no group).
    """
    if not enabled(settings) or not root:
        return None
    if state(root) is None:
        return {'error': 'git unavailable or not a repository'}
    report = {'branch': branch_for(settings, task)}
    current = state(root)[0]
    if current != report['branch']:
        report['note'] = f'checkout is on {current}, committing there'
        report['branch'] = current
    group = (task.get('context_group') or '').strip()
    unit_done = group_done if group else True
    per_task = settings.get('git_commit', 'Task') == 'Task' or not group
    if per_task:
        report['commit'] = _commit(root, settings, task, f"{task['code']}: {task['title']}")
    elif unit_done:
        whole = dict(task, files=[])  # group commit: everything in the checkout
        titles = ', '.join(m['code'] for m in group_members)[:200]
        report['commit'] = _commit(root, settings, whole, f"{group}: {titles}")
    else:
        report['commit'] = {'committed': False, 'reason': 'group commit deferred until the context group is complete'}
    policy = settings.get('git_push', 'Group')
    if policy == 'Never':
        report['push'] = {'pushed': False, 'reason': 'push disabled'}
    elif policy == 'Task' or unit_done:
        PENDING.append({'root': root, 'remote': settings.get('git_remote', 'origin'), 'branch': report['branch'], 'project': project, 'task': task['id'], 'actor': actor})
        report['push'] = {'queued': True}
    else:
        report['push'] = {'pushed': False, 'reason': 'waiting for the rest of the context group'}
    return report

def describe(report):
    if not report:
        return ''
    if 'error' in report and 'commit' not in report:
        return 'git: ' + report['error']
    parts = ['git: branch ' + report['branch']]
    commit = report.get('commit', {})
    if commit.get('committed'):
        parts.append(f"committed {commit['sha']} ({commit.get('summary') or 'no stat'})")
    elif 'error' in commit:
        parts.append('COMMIT FAILED: ' + commit['error'])
    else:
        parts.append(commit.get('reason', 'no commit'))
    push = report.get('push', {})
    if push.get('queued'):
        parts.append('push queued')
    elif push.get('reason'):
        parts.append('push: ' + push['reason'])
    if report.get('note'):
        parts.append(report['note'])
    return '; '.join(parts) + '.'

def flush(call):
    """Run queued pushes after the transaction that queued them; report via task activity."""
    reports = []
    while PENDING:
        item = PENDING.pop(0)
        try:
            result = _git(item['root'], 'push', '--quiet', '-u', item['remote'], item['branch'], timeout=60)
            if result.returncode:
                outcome = {'pushed': False, 'error': (result.stderr or result.stdout).strip()[-300:] or 'push failed'}
            else:
                outcome = {'pushed': True, 'remote': item['remote'], 'branch': item['branch']}
        except (OSError, subprocess.SubprocessError) as error:
            outcome = {'pushed': False, 'error': str(error)[:300]}
        text = f"git: pushed {item['branch']} to {item['remote']}." if outcome.get('pushed') else f"git: PUSH FAILED for {item['branch']} to {item['remote']}: {outcome.get('error')}"
        try:
            call('git_note', {'project': item['project'], 'id': item['task'], 'actor': item['actor'], 'text': text})
        except Exception:
            pass
        reports.append(dict(outcome, task=item['task']))
    return reports
