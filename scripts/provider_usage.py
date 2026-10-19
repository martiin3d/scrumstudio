"""Read actual provider quotas without a model request or credential scraping."""
import json
import math
from pathlib import Path
import shutil
import time
from mcp_client import JsonClient
from cli_runtime import codex_executable

def codex_usage():
    try:executable=codex_executable()
    except RuntimeError:return {}
    if not executable:
        return {}
    client=JsonClient([executable,'app-server'])
    try:
        client.rpc('initialize',{'clientInfo':{'name':'scrum_usage','version':'1'}})
        client.send({'method':'initialized','params':{}})
        result=client.rpc('account/rateLimits/read')
        return normalize_codex(result)
    except (RuntimeError,OSError):
        return {}
    finally:
        client.close()

def normalize_codex(data):
    result={}
    buckets=data.get('rateLimitsByLimitId') or {'default':data.get('rateLimits')}
    for bucket in buckets.values():
        if not isinstance(bucket,dict):
            continue
        for window in [bucket.get('primary'),bucket.get('secondary')]:
            if not isinstance(window,dict):
                continue
            field={300:'session',10080:'weekly'}.get(window.get('windowDurationMins'))
            percent=window.get('usedPercent')
            reset=window.get('resetsAt')
            if field and type(percent) in [int,float] and math.isfinite(percent) and 0<=percent<=100 and (reset is None or reset>time.time()):
                key=field+'_used_percent'
                if key not in result or percent>result[key]:
                    result[key]=percent
                    if reset is not None:
                        result[field+'_resets_at']=reset
    if result:
        result['observed_at']=time.time()
    return result

def claude_payload(data):
    result={}
    rates=data.get('rate_limits') or {}
    for source,target in [('five_hour','session'),('seven_day','weekly')]:
        window=rates.get(source) or {}
        percent=window.get('used_percentage')
        if type(percent) in [int,float] and math.isfinite(percent) and 0<=percent<=100:
            result[target+'_used_percent']=percent
            reset=window.get('resets_at')
            if type(reset) in [int,float] and math.isfinite(reset):
                result[target+'_resets_at']=reset
    if result:
        result['observed_at']=time.time()
    return result

def usage_file(path):
    try:
        path=Path(path)
        if path.stat().st_size>10000:
            return {}
        data=json.loads(path.read_text(encoding='utf-8'))
        if type(data.get('observed_at')) not in [int,float] or not math.isfinite(data['observed_at']) or data['observed_at']>time.time()+5:
            return {}
        allowed=['session_used_percent','weekly_used_percent','session_resets_at','weekly_resets_at','observed_at']
        return {k:data[k] for k in allowed if k in data}
    except (OSError,ValueError,TypeError):
        return {}
