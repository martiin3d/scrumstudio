"""Codex app-server adapter: native reviews and live questions in project Chat.

No automatic acceptance here: auto_review belongs to Codex. Human decisions
return only to the exact live callback; no rules or permanent grants are saved.
"""
import argparse
import json
import queue
import signal
import subprocess
import sys
import threading
import time
from mcp_client import ScrumClient
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import idle_signals


APPROVALS={'item/commandExecution/requestApproval','item/fileChange/requestApproval','item/permissions/requestApproval'}


def request_questions(method,params):
    if method in APPROVALS:
        details={key:params[key] for key in ('command','cwd','reason','permissions','grantRoot','fileChanges') if params.get(key) is not None}
        text=json.dumps(details,ensure_ascii=False,indent=2)
        if len(text)>11000:raise ValueError('Approval details exceed the display limit; cannot approve a truncated request')
        return [{'kind':'permission','question':method+'\n'+text,'options':['Approve once','Deny','Cancel run']}]
    if method=='item/tool/requestUserInput':
        questions=params.get('questions',[])
        if not 1<=len(questions)<=3 or any(q.get('isSecret') for q in questions):raise ValueError('Secret or oversized questions need the provider secure flow')
        return [{'kind':'question','question':q['question'],'options':[o['label'] for o in (q.get('options') or [])]} for q in questions]
    raise ValueError('Unsupported server request; not approved')


def approval_result(method,params,answers):
    answer=answers[0]
    if method=='item/permissions/requestApproval':
        return {'permissions':params.get('permissions',{}) if answer=='Approve once' else {},'scope':'turn'}
    if method in APPROVALS:return {'decision':{'Approve once':'accept','Deny':'decline','Cancel run':'cancel'}[answer]}
    return {'answers':{q['id']:{'answers':[a]} for q,a in zip(params['questions'],answers)}}


def event(kind,**fields):
    print(json.dumps({'type':kind,**fields}),flush=True)


def run(command,project,actor,cwd,prompt,model='',reviewer='auto_review',rpc=None,signals=None,full_access=False):
    owned=rpc is None
    child=None;pending={};messages=queue.Queue();usage={};exit_code=1;items={}
    def send(value):
        child.stdin.write(json.dumps(value)+'\n');child.stdin.flush()
    def request(name,params,key):send({'jsonrpc':'2.0','id':key,'method':name,'params':params})
    def cancel_callbacks():
        for item in pending.values():
            for key in item['keys']:
                try:rpc.call('agent_interaction',{'project':project,'actor':actor,'action':'cancel','id':key})
                except RuntimeError:pass
    try:
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,text=True,encoding='utf-8',errors='replace',bufsize=1)
        def read():
            try:
                for line in child.stdout:
                    try:messages.put(json.loads(line))
                    except ValueError:continue
            finally:messages.put(None)
        threading.Thread(target=read,daemon=True).start()
        request('initialize',{'clientInfo':{'name':'scrum-studio','version':'1'},'capabilities':{'experimentalApi':True}},'scrum:init')
        next_check=0;last_signal=idle_signals.stamp(signals or cwd)
        while True:
            try:message=messages.get(timeout=.25)
            except queue.Empty:message={}
            if message is None:raise RuntimeError('Codex app-server exited before a completed turn')
            if 'id' in message and 'method' not in message:
                if 'error' in message:raise RuntimeError(str(message['error'].get('message','Codex request failed'))[:700])
                result=message.get('result',{})
                if message['id']=='scrum:init':
                    send({'jsonrpc':'2.0','method':'initialized'})
                    request('thread/start',{'cwd':str(cwd),'approvalPolicy':'never' if full_access else 'on-request','approvalsReviewer':reviewer,**({'model':model} if model else {})},'scrum:thread')
                elif message['id']=='scrum:thread':
                    thread=result['thread']['id'];event('thread.started',thread_id=thread)
                    if result.get('model'):event('system',subtype='init',model=result['model'])
                    request('turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}]},'scrum:turn')
            method=message.get('method','');params=message.get('params',{})
            # App-server also broadcasts delegated threads. Their completion must
            # never close the owning worker or replace its answer/usage report.
            if method in ('item/started','item/completed','thread/tokenUsage/updated','turn/completed') and params.get('threadId')!=thread:
                continue
            if 'id' in message and method:
                keys=[]
                try:
                    if method=='item/fileChange/requestApproval':
                        params=dict(params,fileChanges=items.get(params.get('itemId'),{}).get('changes'))
                    for question in request_questions(method,params):
                        if rpc is None:rpc=ScrumClient(timeout=10)
                        saved=rpc.call('agent_interaction',{'project':project,'actor':actor,'action':'request',**question})
                        keys.append(saved['id'])
                    pending[message['id']]={'method':method,'params':params,'keys':keys}
                except (ValueError,RuntimeError) as error:
                    for key in keys:
                        try:rpc.call('agent_interaction',{'project':project,'actor':actor,'action':'cancel','id':key})
                        except RuntimeError:pass
                    send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32603,'message':str(error)[:500]}})
            elif method in ('item/started','item/completed'):
                item=params.get('item',{})
                if item.get('id') and item.get('type')=='fileChange':items[item['id']]=item
                if method=='item/completed' and item.get('type')=='agentMessage':event('item.completed',item={'type':'agent_message','text':item.get('text','')})
            elif method=='thread/tokenUsage/updated':
                total=params.get('tokenUsage',{}).get('total',{})
                usage={out:total.get(key) for out,key in [('input_tokens','inputTokens'),('output_tokens','outputTokens'),('cached_input_tokens','cachedInputTokens'),('reasoning_output_tokens','reasoningOutputTokens')]}
            elif method=='turn/completed':
                turn=params.get('turn',{})
                if turn.get('status')=='completed':event('turn.completed',usage=usage);exit_code=0
                else:event('turn.failed',error={'message':str(turn.get('error') or turn.get('status','Turn interrupted'))[:700]})
                break
            current_signal=idle_signals.stamp(signals or cwd) if pending else last_signal
            if pending and (time.monotonic()>=next_check or current_signal!=last_signal):
                next_check=time.monotonic()+60;last_signal=current_signal
                for callback,item in list(pending.items()):
                    rows=[rpc.call('agent_interaction',{'project':project,'actor':actor,'action':'get','id':key}) for key in item['keys']]
                    if any(r['state'] in ('expired','cancelled') for r in rows):
                        result=approval_result(item['method'],item['params'],['Cancel run']) if item['method'] in APPROVALS else {'answers':{}}
                    elif all(r['state']=='answered' for r in rows):result=approval_result(item['method'],item['params'],[r['answer'] for r in rows])
                    else:continue
                    send({'jsonrpc':'2.0','id':callback,'result':result});del pending[callback]
                    items.pop(item['params'].get('itemId'),None)
                    if any(r['state'] in ('expired','cancelled') or r.get('answer')=='Cancel run' for r in rows):
                        p=item['params']
                        request('turn/interrupt',{'threadId':p['threadId'],'turnId':p['turnId']},'scrum:interrupt:'+str(callback))
        return exit_code
    finally:
        cancel_callbacks()
        if child:
            try:child.stdin.close()
            except OSError:pass
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
            if child.stdout:child.stdout.close()
        if owned and rpc is not None:rpc.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=int,required=True);parser.add_argument('--actor',type=int,required=True)
    parser.add_argument('--cwd',required=True);parser.add_argument('--model',default='')
    parser.add_argument('--signals')
    parser.add_argument('--full-access',action='store_true')
    parser.add_argument('--reviewer',choices=['user','auto_review'],default='auto_review')
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args();command=args.command[1:] if args.command[:1]==['--'] else args.command
    if hasattr(signal,'SIGBREAK'):
        def interrupt(*_):raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK,interrupt)
    try:return run(command,args.project,args.actor,args.cwd,sys.stdin.buffer.read().decode('utf-8'),args.model,args.reviewer,signals=args.signals,full_access=args.full_access)
    except KeyboardInterrupt:return 130
    except (OSError,ValueError,RuntimeError) as error:event('error',message=str(error)[:700]);return 1


if __name__=='__main__':raise SystemExit(main())
