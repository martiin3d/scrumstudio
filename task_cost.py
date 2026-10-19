"""Optional estimates and attributable client-reported tokens; never infer quota."""
import math

def validate(data):
    if not isinstance(data,dict) or set(data)!={'estimated_tokens','estimated_quota_percent','reports'}:
        raise ValueError('Invalid task cost data')
    n=data['estimated_tokens']
    if n is not None and (type(n) is not int or not 0<=n<=10**15):raise ValueError('Invalid estimated tokens')
    n=data['estimated_quota_percent']
    if n is not None and (type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=100):raise ValueError('Invalid estimated quota')
    reports=data['reports']
    if not isinstance(reports,list) or len(reports)>1000:raise ValueError('Too many usage reports')
    keys=set()
    for r in reports:
        if not isinstance(r,dict) or set(r)!={'agent','run_id','source','input_tokens','output_tokens'}:raise ValueError('Invalid usage report')
        for k in ('agent','run_id','source'):
            if not isinstance(r[k],str) or not r[k].strip() or len(r[k])>240:raise ValueError('Invalid report identity/source')
        for k in ('input_tokens','output_tokens'):
            if type(r[k]) is not int or not 0<=r[k]<=10**15:raise ValueError('Invalid token count')
        key=(r['agent'],r['run_id'])
        if key in keys:raise ValueError('Duplicate usage execution')
        keys.add(key)
    return data

def empty():return {'estimated_tokens':None,'estimated_quota_percent':None,'reports':[]}

def update(data,args,identity):
    import copy
    data=copy.deepcopy(data)
    for k in ('estimated_tokens','estimated_quota_percent'):
        if k in args:data[k]=args[k]
    if 'usage_report' in args:
        r=args['usage_report']
        if not isinstance(r,dict) or set(r)!={'run_id','source','input_tokens','output_tokens'}:raise ValueError('Supply exact client-reported input/output totals and run identity')
        r=dict(r,agent=identity)
        validate(dict(data,reports=[r]))
        old=next((x for x in data['reports'] if (x['agent'],x['run_id'])==(identity,r['run_id'])),None)
        if old:
            if old['source']!=r['source'] or any(r[k]<old[k] for k in ('input_tokens','output_tokens')):raise ValueError('Usage snapshots must keep source and increase monotonically')
            old.update(r)
        else:data['reports'].append(r)
    return validate(data)

def summary(data):
    reports=data['reports']
    total=sum(r['input_tokens']+r['output_tokens'] for r in reports) if reports else None
    estimate=data['estimated_tokens']
    return {'reported_tokens':total,'reported_runs':len(reports),'token_variance':total-estimate if total is not None and estimate is not None else None,'coverage':'client-reported runs only' if reports else 'unavailable','quota_attribution':'account-wide; not attributable to a task'}
