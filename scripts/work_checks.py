"""Native batch file checks and representative visual planning, without a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def load(path):
    path=Path(path)
    if path.stat().st_size>10*1024*1024:raise ValueError('Input exceeds 10 MB')
    rows=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Use an array of at most 10000 records')
    return rows


def files(root,rows):
    root=Path(root).resolve();results=[];seen=set()
    for spec in rows:
        if not isinstance(spec,dict) or not isinstance(spec.get('path'),str):raise ValueError('Each file needs path and optional sha256')
        name=spec['path'];path=(root/name).resolve()
        if not path.is_relative_to(root) or path==root:raise ValueError('File escapes root')
        if name in seen:raise ValueError('Duplicate file path')
        seen.add(name);expected=spec.get('sha256')
        if expected is not None and (not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdefABCDEF' for c in expected)):
            raise ValueError('Invalid expected SHA256')
        try:
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
            actual=digest.hexdigest()
            results.append({'path':name,'sha256':actual,'passed':expected is None or actual==expected.lower(),'checked':bool(expected)})
        except OSError as error:results.append({'path':name,'passed':False,'error':type(error).__name__})
    return {'kind':'file_integrity','passed':all(r['passed'] for r in results),'files':len(results),
            'expected_hashes':sum(r.get('checked',False) for r in results),'items':results,
            'basis':'Existence/readability and supplied hashes only; no visual, compiler or functional acceptance inferred.'}


def visual(rows,all_cases=False,samples=6):
    if not 1<=samples<=100:raise ValueError('samples must be 1..100')
    groups={};selected={};seen=set()
    for row in rows:
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and row[k] for k in ('id','family','variant')):raise ValueError('Each case needs id, family, variant')
        if row['id'] in seen:raise ValueError('Duplicate case id')
        seen.add(row['id'])
        if any(k in row and type(row[k]) is not bool for k in ('required','changed_risk')):raise ValueError('required/changed_risk must be boolean')
        groups.setdefault((row['family'],row['variant']),[]).append(row)
        if all_cases or row.get('required') or row.get('changed_risk'):selected[row['id']]='mandatory' if row.get('required') or all_cases else 'changed layout risk'
    # Never silently discard a family/variant or mandatory acceptance to hit a cap.
    for group in groups.values():
        if not any(r['id'] in selected for r in group):selected[group[0]['id']]='family/variant sample'
    return {'kind':'visual_plan','cases':len(rows),'selected':[{'id':key,'reason':reason} for key,reason in selected.items()],
            'sample_target':samples,'target_exceeded':len(selected)>samples,'all_cases':all_cases,
            'instruction':'Run structural/binding assertions over all generated outputs. Inspect selected captures once; retain reviewed artifacts. Expand only for actual differences, changed layout risks or explicit acceptance. A plan is not proof of passing review.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['files','visual']);parser.add_argument('--input',required=True)
    parser.add_argument('--root',default='.');parser.add_argument('--report',required=True)
    parser.add_argument('--all',action='store_true');parser.add_argument('--samples',type=int,default=6)
    args=parser.parse_args()
    try:
        rows=load(args.input);result=files(args.root,rows) if args.mode=='files' else visual(rows,args.all,args.samples)
        report=Path(args.report);report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        summary={k:v for k,v in result.items() if k not in ('items','selected')}
        if 'items' in result:summary['failures']=[r for r in result['items'] if not r['passed']][:10]
        if 'selected' in result:summary.update(selected_count=len(result['selected']),selected=result['selected'][:20])
        print(json.dumps(summary|{'report':str(report)}))
        return 0 if result.get('passed',True) else 1
    except (ValueError,OSError,TypeError) as error:
        print(json.dumps({'passed':False,'error':str(error)[:500]}));return 1


if __name__=='__main__':sys.exit(main())
