"""Task routing without inference; explicit human choices always win."""
import json
import os
from pathlib import Path
from scripts.cli_runtime import model_name

TIERS=('auto','economy','standard','advanced')
FIELDS=('model','model_tier','model_reason','model_selection')

def validate(model='',tier='auto',reason=''):
    model_name(model)
    if tier not in TIERS or not isinstance(reason,str) or len(reason)>500:
        raise ValueError('Invalid automatic model tier/reason')
    if tier!='auto' and not reason.strip():raise ValueError('Automatic tier needs a reason')

def request(task):
    return {key:task.get(key,default) for key,default in [('model',''),('model_tier','auto'),('model_reason','')]}

def automatic_tier(task):
    if task.get('model_tier','auto')!='auto':return task['model_tier'],'Agent/planner choice: '+task['model_reason']
    return 'standard','Default standard route; task size does not determine technical difficulty'

def catalog():
    try:
        path=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'models_cache.json'
        data=json.loads(path.read_text(encoding='utf-8'))
        return {item['slug'] for item in data.get('models',[]) if isinstance(item,dict) and isinstance(item.get('slug'),str)}
    except (OSError,ValueError,TypeError):return set()

def select(task,client,profile_model='',available=None):
    if client not in ('codex','claude'):raise ValueError('Invalid model provider')
    validate(task.get('model',''),task.get('model_tier','auto'),task.get('model_reason',''))
    tier,reason=automatic_tier(task)
    source='task' if task.get('model') else 'profile' if profile_model else 'automatic'
    chosen=task.get('model') or profile_model
    if not chosen:
        if client=='claude':chosen={'economy':'haiku','standard':'sonnet','advanced':'opus'}[tier]
        else:
            known=catalog() if available is None else available
            routes={'economy':['gpt-6-luna','gpt-5.6-luna','gpt-5.4-mini'],
                    'standard':['gpt-6.1-sol','gpt-6-sol','gpt-5.6-sol'],
                    'advanced':['gpt-6-astra','gpt-6.1-sol','gpt-5.6-sol']}
            chosen=next((name for name in routes[tier] if name in known),'')
            if not chosen:reason+='; no known local catalog route, provider default retained'
    model_name(chosen)
    if chosen.startswith('claude-') and client!='claude' or chosen.startswith('gpt-') and client!='codex':
        raise ValueError('Task model belongs to another provider; choose a matching agent')
    return {'client':client,'model':chosen,'tier':tier,'source':source,
            'reason':reason if source=='automatic' else 'Human '+source+' model override',
            'request':request(task)}

def profile_model(folder,actor):
    path=Path(folder)/'.scrum/local/agent-profiles.db'
    if not path.is_file():return ''
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as db:
        return next((r[0] for r in db.execute('SELECT model FROM profiles WHERE actor=?',(actor,))), '')
