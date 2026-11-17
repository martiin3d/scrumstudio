"""Compare paired, independently checked Scrum worker trials."""
import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


CASES = {
    'new-claim': {
        'setup': 'Give the worker a supervisor claim for a one-file change with explicit acceptance.',
        'checks': ['acceptance_passed', 'only_planned_files_changed', 'verified_finish'],
        'redundant': ['start', 'claim', 'show_same_scope', 'board_scan'],
    },
    'resume': {
        'setup': 'Give the worker a claim with unchanged inputs, a valid passing receipt and one remaining acceptance check.',
        'checks': ['acceptance_passed', 'prior_evidence_preserved', 'remaining_check_executed', 'verified_finish'],
        'redundant': ['start', 'claim', 'show_same_scope', 'repeat_valid_verification'],
    },
    'git-recovery': {
        'setup': 'Give the worker a scoped recovery claim with an existing verified commit, recorded destination and unrelated staging.',
        'checks': ['verified_source_integrated', 'unrelated_staging_preserved', 'verified_finish'],
        'redundant': ['start', 'claim', 'show_same_scope', 'repeat_implementation', 'repeat_valid_verification'],
    },
}


def count(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('Counters must be nonnegative integers or null')
    return value


def elapsed(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError('Elapsed seconds must be finite, nonnegative or null')
    return value


def capture_layer(kind, name, workflow, controls, loaded):
    """Fingerprint the exact bytes handed to the worker, with no prompt text in reports."""
    if kind not in ('rule', 'skill') or not re.fullmatch(r'[a-z0-9][a-z0-9._:-]{0,79}', name):
        raise ValueError('Name one rule or skill')
    if not isinstance(workflow, str) or not workflow.strip() or len(workflow) > 120:
        raise ValueError('Identify the workflow used for both runs')
    if not {'launch', 'verification'} <= controls.keys() or not all(
        isinstance(key, str) and isinstance(value, bytes) for key, value in controls.items()
    ):
        raise ValueError('Supply the fixed launch and verification inputs')
    fixed = {key: hashlib.sha256(value).hexdigest() for key, value in controls.items()}
    return {'kind': kind, 'name': name, 'workflow': workflow, 'controls': fixed,
            'loaded_sha256': hashlib.sha256(loaded).hexdigest() if loaded is not None else None}


def validate_layer(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'name', 'workflow', 'controls', 'loaded_sha256'}:
        raise ValueError('Supply the measured rule or skill launch manifest')
    capture_layer(value['kind'], value['name'], value['workflow'], {'launch': b'', 'verification': b''}, None)
    controls = value['controls']
    if not isinstance(controls, dict) or not {'launch', 'verification'} <= controls.keys() or not all(
        isinstance(key, str) and isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest)
        for key, digest in controls.items()
    ):
        raise ValueError('Invalid fixed input fingerprints')
    revision = value['loaded_sha256']
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision)):
        raise ValueError('Invalid loaded revision fingerprint')
    return value


def score(trial, case):
    checks = trial['checks']
    if set(checks) != set(case['checks']) or any(type(value) is not bool for value in checks.values()):
        raise ValueError('Supply an observed boolean for every acceptance check')
    calls = trial['calls']
    if not isinstance(calls, list) or not all(isinstance(call, str) and call for call in calls):
        raise ValueError('Supply the complete observed list of tool call labels')
    usage = trial.get('usage') or {}
    inputs = count(usage.get('input_tokens'))
    cached = count(usage.get('cached_input_tokens'))
    written = count(usage.get('cache_write_input_tokens'))
    if trial['client'] == 'claude' and inputs is not None and not trial.get('input_includes_cache'):
        inputs = inputs + cached + written if cached is not None and written is not None else None
    if inputs is not None and cached is not None and cached > inputs:
        raise ValueError('Cached input exceeds total input')
    return {
        'passed': all(checks.values()),
        'failed_checks': [key for key, passed in checks.items() if not passed],
        'calls': len(calls),
        'redundant_calls': sum(call in case['redundant'] for call in calls),
        'input_tokens': inputs,
        'uncached_input_tokens': inputs - cached if inputs is not None and cached is not None else None,
        'output_tokens': count(usage.get('output_tokens')),
    }


def compare(baseline, candidate, *, behavioral=False):
    def index(rows):
        result = {}
        for row in rows:
            key = (row['case'], row['replicate'])
            if key[0] not in CASES or type(key[1]) is not int or key[1] < 1 or key in result:
                raise ValueError('Each known case and positive replicate must be unique')
            result[key] = row
        return result

    before, after = index(baseline), index(candidate)
    if not before or before.keys() != after.keys():
        raise ValueError('Baseline and candidate must have identical case/replicate pairs')
    if not behavioral and {case for case, _ in before} != set(CASES):
        raise ValueError('Run every published evaluation case')
    comparisons = []
    experiment = None
    for key, old in sorted(before.items()):
        new = after[key]
        for field in ('client', 'model', 'fixture_sha256'):
            if not isinstance(old[field], str) or not old[field] or old[field] != new[field]:
                raise ValueError(f'{key}: mismatched {field}')
        if old['client'] not in ('codex', 'claude'):
            raise ValueError('Use Codex or Claude provider counters')
        left, right = (score(row, CASES[key[0]]) for row in (old, new))
        if behavioral:
            layers = [validate_layer(row.get('layer')) for row in (old, new)]
            metadata = [{k: v for k, v in layer.items() if k != 'loaded_sha256'} for layer in layers]
            revisions = [layer['loaded_sha256'] for layer in layers]
            if metadata[0] != metadata[1] or revisions[0] == revisions[1]:
                raise ValueError('Change one named rule or skill and keep the workflow and other inputs fixed')
            identity = (metadata[0]['kind'], metadata[0]['name'], metadata[0]['workflow'], *revisions)
            if experiment is not None and experiment != identity:
                raise ValueError('Compare the same rule or skill revisions in every pair')
            experiment = identity
            for result, row, layer in zip((left, right), (old, new), layers):
                result.update(layer=layer, tool_actions=count(row.get('tool_actions')), seconds=elapsed(row.get('seconds')))
        metrics = ('calls', 'redundant_calls', 'input_tokens', 'uncached_input_tokens', 'output_tokens')
        if behavioral:metrics += ('tool_actions', 'seconds')
        delta = {name: right[name] - left[name] if left[name] is not None and right[name] is not None else None
                 for name in metrics}
        comparable = left['passed'] and right['passed']
        comparisons.append({'case': key[0], 'replicate': key[1], 'baseline': left, 'candidate': right,
                            'quality_regression': left['passed'] and not right['passed'],
                            'matched_success': comparable, 'candidate_minus_baseline': delta if comparable else None})
    report = {'comparisons': comparisons, 'matched_successes': sum(item['matched_success'] for item in comparisons),
              'quality_regressions': sum(item['quality_regression'] for item in comparisons)}
    if behavioral:
        changes = [pair['candidate_minus_baseline']['uncached_input_tokens'] for pair in comparisons
                   if pair['matched_success']]
        if report['quality_regressions']:
            advice = ('keep_baseline', 'A verified control passed where the candidate failed.')
        elif len(changes) < 2 or len(changes) != len(comparisons) or any(value is None for value in changes):
            advice = ('collect_more', 'Need at least two passing matched pairs with known uncached input.')
        elif all(value < 0 for value in changes):
            advice = ('remove' if experiment[-1] is None else 'keep_candidate',
                      'All measured pairs passed with lower uncached input.')
        else:
            advice = ('trim_or_retest', 'Measure a narrower variant or gather more evidence on mixed costs.')
        report['recommendation'] = dict(zip(('action', 'reason'), advice))
    return report


def git(root,*args,check=True):
    return subprocess.run(['git','-C',str(root),*args],text=True,encoding='utf-8',capture_output=True,
                          timeout=10,check=check)


def use_fixture(root):
    root=root.resolve()
    local=root/'.scrum-eval'
    marker=local/'fixture.json'
    if not marker.is_file() or marker.is_symlink():raise ValueError('Use a prepared evaluation fixture')
    data=json.loads(marker.read_text(encoding='utf-8'))
    if data.get('case') not in CASES:raise ValueError('Unknown fixture case')
    os.environ['SCRUM_REGISTRY']=str(local/'registry.db')
    os.environ['SCRUM_DB']=str(local/'legacy.db')
    return data


def prepare_fixture(root,case):
    from store import run
    root=root.resolve()
    if not root.is_dir() or any(root.iterdir()):raise ValueError('Prepare in an existing empty directory')
    local=root/'.scrum-eval'
    local.mkdir()
    fixture={'case':case}
    (local/'fixture.json').write_text(json.dumps(fixture),encoding='utf-8')
    use_fixture(root)
    (root/'.gitignore').write_text('.scrum/\n.scrum-eval/\n__pycache__/\n',encoding='utf-8')
    expected={'new-claim':'assert first() == 42','resume':'assert first() == 42 and second() == 7'}
    code='def first():\n    return '+('0' if case=='new-claim' else '42')+'\n'
    if case=='resume':code+='\ndef second():\n    return 0\n'
    if case=='git-recovery':code='def first():\n    return 0\n'
    (root/'feature.py').write_text(code,encoding='utf-8')
    if case!='git-recovery':
        (root/'verify_fixture.py').write_text('from feature import *\n'+expected[case]+'\n',encoding='utf-8')
    else:
        (root/'personal.txt').write_text('base\n',encoding='utf-8')
    git(root,'init','-qb','main')
    git(root,'config','user.name','Evaluation fixture')
    git(root,'config','user.email','fixture@example.invalid')
    git(root,'add','.')
    git(root,'commit','-qm','Evaluation fixture base')
    fixture['base_sha']=git(root,'rev-parse','HEAD').stdout.strip()
    if case=='git-recovery':
        git(root,'switch','-qc','verified-source')
        (root/'feature.py').write_text('def first():\n    return 42\n',encoding='utf-8')
        git(root,'commit','-qam','Verified source')
        fixture['verified_sha']=git(root,'rev-parse','HEAD').stdout.strip()
        git(root,'switch','-q','main')
        (root/'personal.txt').write_text('unrelated staged content\n',encoding='utf-8')
        git(root,'add','personal.txt')
    fixture['fixture_sha256']=hashlib.sha256(json.dumps({
        'case':case,'scenario':CASES[case],'inputs':{path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                                                  for path in sorted(root.iterdir()) if path.is_file()},
    },sort_keys=True).encode()).hexdigest()
    project=run('project_create',{'path':str(root),'name':'Worker evaluation'})['id']
    human=run('user_register',{'identity':'human:evaluation','name':'Evaluation reviewer','kind':'human'})['id']
    description={
        'new-claim':'Implement first() returning 42. Run python verify_fixture.py and record evidence.',
        'resume':'first() already returns 42 and its check passed. Implement second() returning 7. Preserve first() and run python verify_fixture.py for the combined result.',
        'git-recovery':'Integrate the existing verified-source branch into main. Preserve the unrelated staged personal.txt. Do not reimplement the feature.',
    }[case]
    task=run('task_create',{'project':project,'actor':human,'title':case,'description':description,
                           'acceptance_criteria':[description],'files':['feature.py'],
                           **({'category':'Git recovery'} if case=='git-recovery' else {})})
    if case=='resume':
        run('task_update',{'project':project,'actor':human,'id':task['id'],'evidence':'first() returns 42',
                           'continuity':'First check passed. Implement second() and run the combined check.'})
    fixture.update(project=project,task=task['id'],task_code=task['code'])
    (local/'fixture.json').write_text(json.dumps(fixture,indent=2),encoding='utf-8')
    return {**fixture,'registry':str(local/'registry.db'),'legacy_db':str(local/'legacy.db')}


def observe_fixture(root,fixture,task):
    case=fixture['case']
    finished=task['status']=='Done' and bool(task.get('evidence'))
    if case=='git-recovery':
        landed=git(root,'merge-base','--is-ancestor',fixture['verified_sha'],'refs/heads/main',check=False)
        staged=git(root,'show',':personal.txt',check=False)
        return {'verified_source_integrated':landed.returncode==0,
                'unrelated_staging_preserved':staged.returncode==0 and staged.stdout=='unrelated staged content\n',
                'verified_finish':finished}
    # Read the deliverable's AST instead of executing arbitrary worker-authored code.
    import ast
    def constant_returns():
        try:module=ast.parse((root/'feature.py').read_text(encoding='utf-8'))
        except (OSError,UnicodeError,SyntaxError):return {}
        return {node.name:node.body[0].value.value for node in module.body
                if isinstance(node,ast.FunctionDef) and len(node.body)==1 and isinstance(node.body[0],ast.Return)
                and isinstance(node.body[0].value,ast.Constant) and type(node.body[0].value.value) is int}
    code=constant_returns()
    if case=='resume':
        return {'acceptance_passed':code.get('first')==42 and code.get('second')==7,
                'prior_evidence_preserved':code.get('first')==42,
                'remaining_check_executed':False,
                'verified_finish':finished}
    changes=git(root,'diff','--name-only',fixture['base_sha'],'--').stdout.splitlines()
    changes+=git(root,'ls-files','--others','--exclude-standard').stdout.splitlines()
    return {'acceptance_passed':code.get('first')==42,'only_planned_files_changed':set(changes)<={'feature.py'},
            'verified_finish':finished}


def collect_fixture(root,run_id,replicate,observed):
    from store import run
    fixture=use_fixture(root)
    project=fixture['project']
    task=run('task_get',{'project':project,'id':fixture['task']})
    audit=run('coordination_metrics',{'project':project,'include_audit':True})['audit']
    sample=next((item for item in audit['startup_samples'] if item['run_id']==run_id),None)
    if not sample or sample['tasks']!=[fixture['task']] or not sample['session']:
        raise ValueError('Use a recorded, single-task managed run from this fixture')
    metrics=run('coordination_metrics',{'project':project,'filter_session':sample['session']})
    labels={'work_start':'start','task_claim':'claim','snapshot':'board_scan'}
    calls=[labels.get(item['tool'],item['tool']) for item in metrics['channel_tools']
           if item['channel'] in ('cli','mcp') for _ in range(item['calls'])]
    checks=observe_fixture(root,fixture,task)
    if fixture['case']=='resume':
        if type(observed.get('remaining_check_executed')) is not bool:
            raise ValueError('Inspect the native verification evidence and record remaining_check_executed')
        checks['remaining_check_executed']=observed['remaining_check_executed']
    extra=observed.get('verified_redundant_calls',[])
    if not isinstance(extra,list) or any(call not in CASES[fixture['case']]['redundant'] or call in labels.values() for call in extra):
        raise ValueError('Record only additional verified redundant call labels')
    for label in extra:
        operation='task_get' if label=='show_same_scope' else None
        if operation in calls:calls[calls.index(operation)]=label
        else:calls.append(label)
    trial={'case':fixture['case'],'replicate':replicate,'client':sample['client'],'model':sample['model'],
            'fixture_sha256':fixture['fixture_sha256'],'checks':checks,'calls':calls,'input_includes_cache':True,
            'usage':{key:sample.get(key) for key in ('input_tokens','cached_input_tokens','output_tokens')}}
    if 'layer' in observed:
        trial.update(layer=validate_layer(observed['layer']),tool_actions=count(observed.get('tool_actions')),
                     seconds=elapsed(observed.get('seconds')))
    return trial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', action='store_true')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--behavioral', action='store_true', help='Compare one measured rule or skill change')
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--prepare',choices=sorted(CASES))
    parser.add_argument('--collect',metavar='RUN_ID')
    parser.add_argument('--replicate',type=int,default=1)
    parser.add_argument('--observed',type=Path,help='Independent confirmation of verification and semantic repeated calls')
    args = parser.parse_args()
    if args.cases:
        result = CASES
    elif args.fixture and (args.prepare or args.collect):
        try:
            result=prepare_fixture(args.fixture,args.prepare) if args.prepare else collect_fixture(
                args.fixture.resolve(),args.collect,args.replicate,
                json.loads(args.observed.read_text(encoding='utf-8')) if args.observed else {})
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
            parser.error(str(error))
    elif args.baseline and args.candidate:
        try:
            result = compare(*(json.loads(path.read_text(encoding='utf-8')) for path in (args.baseline, args.candidate)),
                             behavioral=args.behavioral)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
    else:
        parser.error('Use --cases or supply both --baseline and --candidate')
    print(json.dumps(result, indent=2))
    return int(bool(result.get('quality_regressions')))


if __name__ == '__main__':
    raise SystemExit(main())
