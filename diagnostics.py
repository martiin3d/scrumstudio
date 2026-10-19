"""Actionable local failures without recording request bodies or exception text."""
import errno
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sqlite3
import sys
import time
import traceback
import uuid

LOG_PATH=Path(__file__).resolve().parent/'data'/'diagnostics.log'


def failure(error,operation):
    sql_code=getattr(error,'sqlite_errorcode',None)
    primary=sql_code & 255 if type(sql_code) is int else None
    message=str(error).lower() if isinstance(error,sqlite3.Error) else ''
    if isinstance(error,PermissionError) or (isinstance(error,OSError) and error.errno in (errno.EACCES,errno.EPERM,errno.EROFS)) or primary==sqlite3.SQLITE_READONLY or 'readonly' in message:
        code='STORAGE_PERMISSION_DENIED'
        text='Scrum cannot write to its registry or the selected project storage.'
        step='Use the normal approval mechanism to grant this process access to the registry and the project .scrum folder; retry only after reconciling the previous operation.'
    elif primary in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or 'database is locked' in message or 'database table is locked' in message:
        code='STORAGE_BUSY'
        text='Another operation currently holds a storage lock.'
        step='Let the current operation finish, then inspect current state before retrying a write. Do not delete database or lock files.'
    elif isinstance(error,sqlite3.IntegrityError):
        code='STORAGE_CONSTRAINT'
        text='The operation conflicts with an existing storage relationship.'
        step='Re-read the project, actor and task in this checkout and verify their identifiers before retrying.'
    elif isinstance(error,(OSError,sqlite3.Error)):
        code='STORAGE_UNAVAILABLE'
        text='Scrum storage could not be opened or updated.'
        step='Check that the configured registry and project folder are available, writable and have free disk space; inspect the diagnostic reference.'
    else:
        code='INTERNAL_ERROR'
        text='Scrum could not finish the operation.'
        step='Inspect the diagnostic reference and reconcile current state before retrying a write.'
    reference=uuid.uuid4().hex
    event={'id':reference,'time':time.time(),'operation':operation,'code':code,
           'exception_type':type(error).__name__,
           'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)[-6:]]}
    if type(sql_code) is int:event['sqlite_code']=sql_code
    if isinstance(error,OSError) and type(error.errno) is int:event['errno']=error.errno
    encoded=json.dumps(event,separators=(',',':'))
    logged=False
    handler=None
    try:
        LOG_PATH.parent.mkdir(parents=True,exist_ok=True)
        handler=RotatingFileHandler(LOG_PATH,maxBytes=1_000_000,backupCount=1,encoding='utf-8')
        handler.handle(logging.LogRecord('scrum.diagnostics',logging.ERROR,'',0,encoded,(),None))
        logged=True
    except Exception:
        pass # A diagnostic must never mask the failed operation.
    finally:
        if handler is not None:
            try:handler.close()
            except Exception:pass
    try:print(encoded,file=sys.stderr)
    except Exception:pass
    return {'code':code,'message':text,'next_step':step,'diagnostic_id':reference,
            'diagnostic_log':str(LOG_PATH) if logged else 'server stderr (file log unavailable)'}
