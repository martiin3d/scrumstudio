"""Bounded, factual views of uncommitted checkout edits for task resumption and review.

Runs in local code during handoffs, resume, claims and task_changes (never a model
call). Missing git, non-repositories, timeouts or errors return '' / {} so
coordination never fails.
"""
import os
from pathlib import Path
import subprocess

def _git(root, *args):
    flags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW for the MCP server
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=3, creationflags=flags)

def _relative(root, path):
    value = Path(path)
    if value.is_absolute():
        try:
            value = value.resolve().relative_to(Path(root).resolve())
        except (OSError, ValueError):
            return ''
    text = value.as_posix().replace('\\', '/')
    return text[2:] if text.startswith('./') else text

def summary(root, planned=(), limit=12, budget=400):
    if not root:
        return ''
    try:
        prefix = _git(root, 'rev-parse', '--show-prefix')
        if prefix.returncode:
            return ''
        prefix = prefix.stdout.strip()  # board folder may be a subfolder of the repository
        strip = lambda path: path[len(prefix):] if prefix and path.startswith(prefix) else path
        status = _git(root, 'status', '--porcelain', '--untracked-files=normal', '--', '.')
        if status.returncode:
            return ''
        changed = []
        for line in status.stdout.splitlines():
            path = line[3:].strip()
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            changed.append(strip(path.strip('"')))
        if not changed:
            return 'Checkout: no uncommitted changes.'
        counts = {}
        diff = _git(root, 'diff', '--numstat', 'HEAD', '--', '.')
        if not diff.returncode:
            for line in diff.stdout.splitlines():
                parts = line.split('\t', 2)
                if len(parts) == 3:
                    counts[strip(parts[2])] = parts[0] + '/' + parts[1]
        wanted = {_relative(root, p) for p in planned} - {''}
        scoped = [p for p in changed if p in wanted]
        label = 'Changed planned files' if scoped else 'Checkout changes (may include other work)'
        items = scoped or changed
        shown = []
        for path in items[:limit]:
            delta = counts.get(path)
            shown.append(path + (' (+' + delta.replace('/', '/-') + ')' if delta and delta != '-/-' else ' (new)' if path not in counts else ''))
        text = label + ': ' + ', '.join(shown) + (f' and {len(items) - limit} more' if len(items) > limit else '') + '.'
        return text if len(text) <= budget else text[:budget - 3] + '...'
    except (OSError, ValueError, subprocess.SubprocessError):
        return ''


def _status(root):
    """Return (prefix, [relative changed paths], {path: 'added/removed'}) or None."""
    prefix = _git(root, 'rev-parse', '--show-prefix')
    if prefix.returncode:
        return None
    prefix = prefix.stdout.strip()
    strip = lambda path: path[len(prefix):] if prefix and path.startswith(prefix) else path
    status = _git(root, 'status', '--porcelain', '--untracked-files=all', '--', '.')
    if status.returncode:
        return None
    changed = []
    for line in status.stdout.splitlines():
        path = line[3:].strip()
        if ' -> ' in path:
            path = path.split(' -> ', 1)[1]
        path = strip(path.strip('"'))
        if path.startswith('.scrum/'):
            continue  # board metadata, never task work
        changed.append(path)
    counts = {}
    diff = _git(root, 'diff', '--numstat', 'HEAD', '--', '.')
    if not diff.returncode:
        for line in diff.stdout.splitlines():
            parts = line.split('\t', 2)
            if len(parts) == 3:
                counts[strip(parts[2])] = parts[0] + '/' + parts[1]
    return prefix, changed, counts

def dirty(root, planned):
    """Planned files that already carry uncommitted edits (a claim-time baseline)."""
    if not root or not planned:
        return []
    try:
        state = _status(root)
        if not state:
            return []
        wanted = {_relative(root, p) for p in planned} - {''}
        return [p for p in state[1] if p in wanted]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

def review(root, planned=(), max_lines=200, budget=12000, include_diff=True, scope=None):
    """Structured, size-capped review of uncommitted edits.

    files: every changed path with +/- counts and whether it was planned.
    diff:  unified diff (2 context lines) of the planned files (or of `scope`
           when given, or of everything when the task has no plan); new files
           show their first lines as '+'.
    The diff is cut at max_lines lines / budget bytes and flags truncated so an
    agent can open specific files instead of reading an unbounded dump.
    """
    if not root:
        return {}
    try:
        state = _status(root)
        if not state:
            return {'git': False}
        prefix, changed, counts = state
        wanted = {_relative(root, p) for p in planned} - {''}
        files = []
        for path in changed[:50]:
            delta = counts.get(path)
            item = {'path': path, 'planned': path in wanted}
            if delta and delta != '-/-':
                item['added'], item['removed'] = [int(x) if x.isdigit() else 0 for x in delta.split('/')]
            elif path not in counts:
                item['new'] = True
            files.append(item)
        result = {'git': True, 'files': files, 'changed_total': len(changed)}
        unplanned = [f['path'] for f in files if wanted and not f['planned']]
        if unplanned:
            result['unplanned'] = unplanned[:20]
        if not changed:
            result['clean'] = True
            return result
        if not include_diff:
            return result
        focus = ({_relative(root, p) for p in scope} - {''}) if scope else wanted
        scope = [p for p in changed if p in focus] if focus else changed
        if focus and not scope:
            result['diff'] = ''
            result['note'] = 'None of the selected files changed; see files/unplanned.'
            return result
        max_lines = max(20, min(int(max_lines or 200), 2000))
        budget = max(2000, min(int(budget or 12000), 200000))
        lines = []
        tracked = [p for p in scope if p in counts]
        if tracked:
            diff = _git(root, 'diff', '--unified=2', '--no-color', 'HEAD', '--', *tracked)
            if not diff.returncode:
                lines.extend(diff.stdout.splitlines())
        for path in scope:
            if path in counts:
                continue
            target = Path(root) / path
            try:
                if target.is_dir() or target.stat().st_size > 512 * 1024:
                    lines.append(f'+++ {path} (new, skipped: too large or a directory)')
                    continue
                body = target.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
            lines.append(f'+++ {path} (new file, {len(body)} lines)')
            lines.extend('+' + line for line in body[:60])
            if len(body) > 60:
                lines.append(f'+... {len(body) - 60} more lines')
        shown = lines[:max_lines]
        text = '\n'.join(shown)
        truncated = len(lines) > max_lines
        if len(text) > budget:
            text = text[:budget]
            truncated = True
        result['diff'] = text
        result['diff_lines'] = len(lines)
        if truncated:
            result['truncated'] = True
            result['hint'] = 'Diff cut at the limit; call again with files=[subset] or read those files directly.'
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
