"""Deterministic isolated payload comparison; no real tasks or provider calls."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_app import WorkflowTest
from store import run

def measure():
    fixture=WorkflowTest();fixture.setUp()
    try:
        p,actor=fixture.p,fixture.actor
        task=run('task_create',dict(project=p,actor=actor,title='Bounded fixture',description='One concrete change',acceptance_criteria=['Verified'],estimated_tokens=1000))['id']
        run('task_update',dict(project=p,id=task,actor=actor,usage_report=dict(run_id='fixture',source='fixture',input_tokens=500,output_tokens=100)))
        settings=run('project_settings',dict(project=p))['settings_version']
        sizes={}
        for mode in ('Light','Standard'):
            human=run('user_register',dict(identity='human:benchmark',name='Benchmark',kind='human'))['id']
            run('project_settings',dict(project=p,actor=human,workflow_mode=mode))
            policy=run('project_settings',dict(project=p))['settings_version']
            for verbose in (True,False):
                result=run('work_start',dict(project=p,actor=actor,client='codex',session_used_percent=1,weekly_used_percent=1,settings_seen=policy,verbose=verbose))
                sizes[mode+('_verbose' if verbose else '_compact')]=len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())
        for include in (False,True):
            result=run('task_get',dict(project=p,id=task,include_cost=include))
            sizes['task_cost' if include else 'task_default']=len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())
        return {'response_bytes':sizes,'note':'Synthetic equal-work payloads, not tokens, quota, or model latency. No automatic claims in this comparison.'}
    finally:fixture.tearDown()

if __name__=='__main__':print(json.dumps(measure()))
