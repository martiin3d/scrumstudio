"""Named, checkout-local CLI profiles. Commands are fixed, never supplied by a role."""
from contextlib import closing
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from scripts.cli_runtime import codex_executable,model_name

ROOT=Path(__file__).resolve().parent

def project_info(project,call):
    item=next((p for p in call('projects',{}) if p['id']==project),None)
    if not item or not item.get('uuid') or not item.get('available'):raise ValueError('An available folder-scoped project is required')
    return item

def connect(folder):
    db=sqlite3.connect(Path(folder)/'.scrum/local/agent-profiles.db',timeout=5)
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,client TEXT NOT NULL,instructions TEXT NOT NULL,source TEXT NOT NULL,usage_file TEXT NOT NULL,actor INTEGER NOT NULL,desired INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0,started REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '')")
    if 'resume_on_wakeup' not in {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}:
        db.execute('BEGIN IMMEDIATE')
        if 'resume_on_wakeup' not in {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}:
            db.execute('ALTER TABLE profiles ADD COLUMN resume_on_wakeup INTEGER NOT NULL DEFAULT 0')
            db.execute('UPDATE profiles SET resume_on_wakeup=desired')
        db.commit()
    if 'model' not in {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}:
        db.execute('BEGIN IMMEDIATE')
        if 'model' not in {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}:
            db.execute("ALTER TABLE profiles ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        db.commit()
    return db

def identity(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('Invalid profile ID')
    return value

def profile(db,value):
    row=db.execute('SELECT * FROM profiles WHERE id=?',(identity(value),)).fetchone()
    if row is None:raise ValueError('Agent profile not found')
    return dict(row)

def view(row,sessions,detail=False):
    session=next((a for a in sessions if a['actor']==row['actor']),{})
    connected=bool(session.get('bridge_connected'))
    state=('failed' if session.get('run_exit_code') not in (None,0) and session.get('status') in ('waiting','offline') else session.get('status','waiting')) if connected else 'starting' if row['desired'] and time.time()-row['started']<20 else 'disconnected' if row['desired'] else 'stopped'
    result={k:row[k] for k in ('id','name','client','source','actor','generation','error','model')}
    result.update(enabled=bool(row['desired']),resume_on_wakeup=bool(row['resume_on_wakeup']),connected=connected,status=state,instruction_chars=len(row['instructions']))
    if session.get('run_exit_code') not in (None,0):result['error']=session.get('run_error') or f"CLI exited with code {session['run_exit_code']}"
    if detail:result.update(instructions=row['instructions'],usage_file=row['usage_file'])
    return result

def launch(db,row,project,folder,confirmed,sessions):
    if row['client']=='claude' and not confirmed:raise ValueError('Explicit authorization to use the configured Claude account is required')
    session=next((s for s in sessions if s['actor']==row['actor'] and s.get('bridge_connected')),None)
    if session:
        if not row['desired']:raise ValueError('Wait for the stopped bridge to disconnect before starting')
        db.execute('UPDATE profiles SET generation=generation+1,error=? WHERE id=?',('',row['id']))
        return 'start_requested'
    if row['desired'] and time.time()-row['started']<20:return 'starting'
    if row['client']=='claude' and not row['usage_file']:raise ValueError('Claude requires a configured absolute usage-file path')
    if not (codex_executable() if row['client']=='codex' else shutil.which(row['client'])):raise ValueError(row['client']+' CLI is not available; configure an installed runtime or PATH')
    db.execute('UPDATE profiles SET desired=1,generation=generation+1,started=?,error=? WHERE id=?',(time.time(),'',row['id']))
    db.commit() # The child must be able to read the profile immediately.
    command=[sys.executable,str(ROOT/'scripts/agent_bridge.py'),'--project',str(project),'--client',row['client'],'--worker','profile-'+row['id'],'--profile',row['id'],'--allow-run','--start-now']
    if row['usage_file']:command+=['--usage-file',row['usage_file']]
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
    log=Path(folder)/'.scrum/local'/('bridge-'+row['id']+'.log')
    try:
        with log.open('ab') as output:subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,**options)
    except OSError as error:
        db.execute('UPDATE profiles SET desired=0,started=0,error=? WHERE id=?',(str(error)[:500],row['id']))
        return 'launch_failed'
    return 'starting'

def operation(a,call):
    p=project_info(a['project'],call);mode=a.get('action','list')
    if mode not in ('list','get','create','update','start','stop'):raise ValueError('Invalid agent profile action')
    if mode in ('create','update','start','stop'):
        human=next((u for u in call('users',{}) if u['id']==a.get('actor') and u['kind']=='human' and u.get('active',1)),None)
        if not human:raise ValueError('Choose an active human to authorize agent control')
    sessions=call('agent_status',{'project':a['project']})['agents']
    with closing(connect(p['path'])) as db,db:
        if mode=='list':
            active={u['id'] for u in call('users',{}) if u.get('active',1)}
            return {'agents':[dict(view(dict(r),sessions),deleted=r['actor'] not in active) for r in db.execute('SELECT * FROM profiles ORDER BY name,id')],'local_only':True}
        if mode=='create':
            name=a.get('name','');instructions=a.get('instructions','');source=a.get('source','');client=a.get('client','codex');usage=a.get('usage_file','');start=a.get('start_immediately',True)
            if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('Name must be 1..80 characters')
            if not isinstance(instructions,str) or len(instructions)>8000 or '\x00' in instructions:raise ValueError('Instructions must be text, max 8000 characters')
            if not isinstance(source,str) or len(source)>240 or (source and not source.lower().endswith(('.md','.toml'))):raise ValueError('Source must be a Markdown or TOML filename')
            if client not in ('codex','claude') or type(start) is not bool:raise ValueError('Invalid client/start setting')
            model=model_name(a.get('model',''))
            if not isinstance(usage,str) or (usage and not Path(usage).is_absolute()):raise ValueError('Usage file must be an absolute path')
            if client=='claude' and start and not a.get('confirm_client_account',False):raise ValueError('Authorize the configured Claude account explicitly before starting')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]>=20:raise ValueError('Maximum 20 local agent profiles per project')
            if db.execute('SELECT 1 FROM profiles WHERE lower(name)=lower(?)',(name.strip(),)).fetchone():raise ValueError('An agent with this name already exists')
            key=uuid.uuid4().hex
            actor=call('user_register',{'identity':f"agent:managed:{p['checkout']}:{key}",'name':name.strip(),'kind':'agent'})['id']
            db.execute('INSERT INTO profiles(id,name,client,instructions,source,usage_file,actor,resume_on_wakeup,model) VALUES(?,?,?,?,?,?,?,?,?)',(key,name.strip(),client,instructions,source.replace('\\','/').split('/')[-1],usage,actor,int(start),model))
            row=profile(db,key);request='created'
            if start:
                try:request=launch(db,row,a['project'],p['path'],a.get('confirm_client_account',False),sessions)
                except ValueError as error:
                    db.execute('UPDATE profiles SET error=? WHERE id=?',(str(error)[:500],key));request='launch_failed'
            result=view(profile(db,key),sessions);result['request']=request
            return result
        row=profile(db,a.get('id'))
        if mode=='get':return view(row,sessions,detail=a.get('include_instructions',False))
        if mode=='update':
            if set(a)-{'project','actor','id','action','model','measurement_session'}:raise ValueError('Profile update accepts only model; active runs and other settings are preserved')
            if 'model' not in a:raise ValueError('Supply model to update, or an empty string for provider default')
            db.execute('UPDATE profiles SET model=? WHERE id=?',(model_name(a['model']),row['id']))
            return dict(view(profile(db,row['id']),sessions),request='updated_next_run')
        if mode=='start' and not any(u['id']==row['actor'] and u.get('active',1) for u in call('users',{})):
            raise ValueError('Agent profile was deleted; a human must restore it before starting')
        db.execute('BEGIN IMMEDIATE')
        row=profile(db,a.get('id'))
        if mode=='stop':
            db.execute('UPDATE profiles SET desired=0,resume_on_wakeup=0 WHERE id=?',(row['id'],));request='stop_requested'
        elif a.get('_restore_only') and not row['resume_on_wakeup']:
            request='skipped_stopped' # Stop may have arrived after roster discovery.
        else:
            db.execute('UPDATE profiles SET resume_on_wakeup=1 WHERE id=?',(row['id'],))
            request=launch(db,row,a['project'],p['path'],a.get('confirm_client_account',False),sessions)
        result=view(profile(db,row['id']),sessions);result['request']=request
        return result

def wakeup(a,call,signal):
    """Restore the enabled roster after a human signal, without weakening quotas."""
    result=signal()
    if not a.get('start_agents',True):return result
    p=next((p for p in call('projects',{}) if p['id']==a['project']),None)
    if not p or not p.get('uuid') or not p.get('available'):return result
    launches=[]
    try:
        profiles=operation({'project':a['project'],'action':'list'},call)['agents']
        if not profiles and not result['wakeup']['connected_bridges']:
            try:
                created=operation({'project':a['project'],'actor':a['actor'],'action':'create','name':'Default Codex agent','client':'codex','start_immediately':True},call)
                launches.append({k:created[k] for k in ('id','name','client','request','error')})
                result['default_profile_created']=True
            except ValueError:
                profiles=operation({'project':a['project'],'action':'list'},call)['agents']
                if not profiles:raise
        for profile in profiles:
            if not profile['resume_on_wakeup']:continue
            entry={k:profile[k] for k in ('id','name','client')}
            if profile['connected']:
                entry['request']='signal_sent'
            elif profile['status']=='starting':
                entry['request']='starting'
            elif profile['client']=='claude' and not a.get('confirm_client_account',False):
                entry.update(request='authorization_required',error='Authorize the configured Claude account before restarting this agent.')
            else:
                try:
                    started=operation({'project':a['project'],'actor':a['actor'],'action':'start','id':profile['id'],'confirm_client_account':a.get('confirm_client_account',False),'_restore_only':True},call)
                    entry.update(request=started['request'],error=started['error'])
                except (OSError,ValueError) as error:
                    entry.update(request='launch_failed',error=str(error)[:500])
            launches.append(entry)
        result['wakeup']=call('agent_status',{'project':a['project']})['wakeup']
        starting=[item for item in launches if item['request']=='starting']
        errors=[item for item in launches if item['request'] in ('launch_failed','authorization_required')]
        if starting:
            result['wakeup'].update(status='starting',message=f'Starting {len(starting)} agent connection(s). Provider execution waits for fresh quota.',next_step='Open Agents or Chat for connection, quota and execution status.')
        elif errors:
            result['wakeup'].update(status='launch_failed',message='Wakeup could not start the requested agent set.')
        elif profiles and not any(item['request']!='skipped_stopped' for item in launches) and not result['wakeup']['connected_bridges']:
            result['wakeup'].update(status='profiles_stopped',message='All saved agents are individually stopped or not enabled for Wakeup.',next_step='Open Agents and use Start / continue on the agents you want to include. Wakeup will remember that enabled set.')
        if errors:
            result['wakeup']['next_step']=' '.join(item['name']+': '+item['error'] for item in errors)+' Open Agents to resolve the failed or unauthorized profiles; successful starts are preserved.'
    except (OSError,ValueError,sqlite3.Error) as error:
        result['wakeup'].update(status='launch_failed',message='Wakeup was recorded, but agent restoration failed.',next_step=str(error)[:500]+' Inspect existing profiles before retrying; some operations may already have completed.')
    result['launches']=launches
    return result


def checkpoint(a,call):
    p=project_info(a['project'],call)
    with closing(connect(p['path'])) as db:row=profile(db,a['profile_id'])
    if row['actor']!=a['actor']:raise ValueError('Agent profile identity mismatch')
    result=call('agent_checkpoint',{k:v for k,v in a.items() if k!='profile_id'})
    result.update(profile_enabled=bool(row['desired']),profile_generation=row['generation'],profile_model=row['model'])
    if not row['desired']:result.update(should_stop=True,reason='Agent profile stopped')
    return result
