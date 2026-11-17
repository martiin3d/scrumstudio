"""Find local skill manifests without loading their instructions into the prompt."""

import argparse
import json
import os
from pathlib import Path
import re


def words(value):
    return set(re.findall(r'[^\W_]+', value.casefold()))


def metadata(manifest):
    try:
        with manifest.open(encoding='utf-8-sig') as source:
            header = source.read(16_384).splitlines()
    except (OSError, UnicodeError):
        return None
    if not header or header[0].strip() != '---' or '---' not in header[1:]:
        return None
    fields = {}
    field = None
    for line in header[1:header.index('---', 1)]:
        match = re.match(r'^(name|description):\s*(.*)$', line)
        if match:
            field, value = match.groups()
            fields[field] = [] if value in ('>', '>-', '|', '|-') else [value]
        elif line.startswith((' ', '\t')) and field in fields:
            fields[field].append(line.strip())
        else:
            field = None
    name = ' '.join(fields.get('name', ())).strip(' \'"')
    description = ' '.join(fields.get('description', ())).strip(' \'"')
    if name and description:
        return {'name': name, 'description': description, 'path': str(manifest.absolute())}
    return None


def catalog(project, client='codex'):
    home = Path.home()
    codex_home = Path(os.environ.get('CODEX_HOME', home / '.codex'))
    provider = (project / '.claude/skills', home / '.claude/skills') if client=='claude' else (codex_home / 'skills',)
    roots = (project / '.agents/skills', project / 'skills', home / '.agents/skills',
             *provider, Path(__file__).resolve().parents[1] / 'skills')
    found = {}
    for root in roots:
        for manifest in sorted(root.glob('*/SKILL.md')):
            item = metadata(manifest)
            if item:
                found.setdefault(item['name'].casefold(), item)
    return found


def lookup(project, query):
    terms = words(query)
    if not terms:
        return []
    matches = []
    for name, item in catalog(project).items():
        score = 4 * len(terms & words(name)) + len(terms & words(item['description']))
        if name == query.casefold():
            score += 100
        if score:
            matches.append((-score, name, item))
    return [item | {'description': item['description'][:180]} for _, _, item in sorted(matches)[:5]]


def required_skill_prompt(project, checkout, client, names):
    from store import validate_required_skills
    defaults = project / '.scrum/local/required-skills.json'
    warning = ''
    try:
        declared = validate_required_skills(json.loads(defaults.read_text(encoding='utf-8-sig')))
    except FileNotFoundError:
        declared = []
    except (OSError, ValueError):
        declared = []
        warning = 'Project required-skills.json is unreadable or invalid.'
    names = {name.casefold(): name for name in (*declared, *names)}
    if not names:
        return warning
    available = catalog(checkout, client)
    manifests = {name: available[key]['path'] for key, name in names.items() if key in available}
    missing = [name for key, name in names.items() if key not in available]
    sections = [warning]
    if manifests:
        sections.append('Read these required SKILL.md manifests before affected work: '+json.dumps(manifests,ensure_ascii=False))
    if missing:
        sections.append('Required skills missing from local manifests: '+', '.join(missing)+'. Check the provider catalog and report any unresolved requirement before affected work.')
    return '\n'.join(section for section in sections if section)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query', help='Skill name or a few capability keywords')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(lookup(args.project, args.query)))
