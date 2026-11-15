"""Compare paired, independently checked Scrum worker trials."""
import argparse
import json
from pathlib import Path


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
    if trial['client'] == 'claude' and inputs is not None:
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


def compare(baseline, candidate):
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
    if {case for case, _ in before} != set(CASES):
        raise ValueError('Run every published evaluation case')
    comparisons = []
    for key, old in sorted(before.items()):
        new = after[key]
        for field in ('client', 'model', 'fixture_sha256'):
            if not isinstance(old[field], str) or not old[field] or old[field] != new[field]:
                raise ValueError(f'{key}: mismatched {field}')
        if old['client'] not in ('codex', 'claude'):
            raise ValueError('Use Codex or Claude provider counters')
        left, right = (score(row, CASES[key[0]]) for row in (old, new))
        delta = {name: right[name] - left[name] if left[name] is not None and right[name] is not None else None
                 for name in ('calls', 'redundant_calls', 'input_tokens', 'uncached_input_tokens', 'output_tokens')}
        comparable = left['passed'] and right['passed']
        comparisons.append({'case': key[0], 'replicate': key[1], 'baseline': left, 'candidate': right,
                            'quality_regression': left['passed'] and not right['passed'],
                            'matched_success': comparable, 'candidate_minus_baseline': delta if comparable else None})
    return {'comparisons': comparisons, 'matched_successes': sum(item['matched_success'] for item in comparisons),
            'quality_regressions': sum(item['quality_regression'] for item in comparisons)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', action='store_true')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    args = parser.parse_args()
    if args.cases:
        result = CASES
    elif args.baseline and args.candidate:
        try:
            result = compare(*(json.loads(path.read_text(encoding='utf-8')) for path in (args.baseline, args.candidate)))
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
    else:
        parser.error('Use --cases or supply both --baseline and --candidate')
    print(json.dumps(result, indent=2))
    return int(bool(result.get('quality_regressions')))


if __name__ == '__main__':
    raise SystemExit(main())
