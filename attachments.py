"""Task references: bounded local blobs, metadata in portable task records."""
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import struct
import uuid

MAX_BYTES=5*1024*1024
MAX_FILES=10
TYPES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.pdf':'application/pdf','.txt':'text/plain','.md':'text/plain','.csv':'text/csv','.json':'application/json','.zip':'application/zip','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


def filename(value):
    if not isinstance(value,str) or not value.strip() or len(value)>160 or any(c in value for c in '/\\:') or any(ord(c)<32 for c in value):
        raise ValueError('Attachment name must be a plain filename (max160), without paths')
    if Path(value).suffix.lower() not in TYPES:raise ValueError('Unsupported attachment type; use PNG/JPEG/GIF/WebP, PDF, text, JSON/CSV, ZIP or Office files')
    return value


def validate_metadata(items):
    if not isinstance(items,list) or len(items)>MAX_FILES:raise ValueError('Invalid attachment metadata')
    seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'id','name','mime','size','sha256','created','author','previewable'}:raise ValueError('Invalid attachment metadata fields')
        key=item['id'];filename(item['name'])
        if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key) or key in seen:raise ValueError('Invalid attachment ID')
        seen.add(key)
        if item['mime']!=TYPES[Path(item['name']).suffix.lower()] or type(item['size']) is not int or not 1<=item['size']<=MAX_BYTES:raise ValueError('Invalid attachment type or size')
        if not isinstance(item['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise ValueError('Invalid attachment digest')
        if not isinstance(item['created'],str) or len(item['created'])>80 or not isinstance(item['author'],str) or len(item['author'])>160:raise ValueError('Invalid attachment authorship')
        if type(item['previewable']) is not bool or item['previewable']!=item['mime'].startswith('image/'):raise ValueError('Invalid image preview metadata')
    return items


def image_size(data,mime):
    width=height=0
    if mime=='image/png' and len(data)>=45 and data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16]==b'IHDR' and data.endswith(b'\0\0\0\0IEND\xaeB`\x82'):
        width,height=struct.unpack('>II',data[16:24])
    elif mime=='image/gif' and len(data)>=14 and data[:6] in (b'GIF87a',b'GIF89a') and data.endswith(b';'):
        width,height=struct.unpack('<HH',data[6:10])
    elif mime=='image/jpeg' and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
        offset=2
        while offset+4<len(data):
            if data[offset]!=255:break
            while offset<len(data) and data[offset]==255:offset+=1
            if offset>=len(data):break
            marker=data[offset];offset+=1
            if marker in (0xd8,0xd9):continue
            if offset+2>len(data):break
            length=int.from_bytes(data[offset:offset+2],'big')
            if length<2 or offset+length>len(data):break
            if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf) and length>=7:
                height,width=struct.unpack('>HH',data[offset+3:offset+7]);break
            offset+=length
    elif mime=='image/webp' and len(data)>=30 and data[:4]==b'RIFF' and data[8:12]==b'WEBP' and int.from_bytes(data[4:8],'little')==len(data)-8:
        if data[12:16]==b'VP8X':width=1+int.from_bytes(data[24:27],'little');height=1+int.from_bytes(data[27:30],'little')
        elif data[12:16]==b'VP8 ' and data[23:26]==b'\x9d\x01\x2a':width=int.from_bytes(data[26:28],'little')&0x3fff;height=int.from_bytes(data[28:30],'little')&0x3fff
        elif data[12:16]==b'VP8L' and data[20]==0x2f:
            bits=int.from_bytes(data[21:25],'little');width=1+(bits&0x3fff);height=1+((bits>>14)&0x3fff)
    if not 1<=width<=16384 or not 1<=height<=16384 or width*height>40_000_000:raise ValueError('Invalid or oversized reference image')


def blob_path(root,key):
    if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Invalid attachment ID')
    if root.is_symlink() or root.resolve().parent!=root.parent.resolve():raise ValueError('Unsafe attachment storage folder')
    path=root/(key+'.bin')
    if path.is_symlink():raise ValueError('Unsafe attachment storage file')
    return path


def operation(db,a,project,main):
    from store import actor_user,lock_access,now,activity
    import runtime
    if set(a)-{'project','id','action','actor','token','attachment_id','name','content_base64','include_content','measurement_session'}:raise ValueError('Unsupported attachment fields; local paths are not accepted')
    if main is None:raise ValueError('Attachments require an available folder-scoped project')
    mode=a.get('action','list')
    if mode not in ('list','add','get','remove'):raise ValueError('Invalid attachment action')
    if type(a.get('include_content',False)) is not bool:raise ValueError('include_content must be boolean')
    db.execute('BEGIN IMMEDIATE')
    task=db.execute('SELECT * FROM tasks WHERE id=? AND project=?',(a.get('id'),project)).fetchone()
    if not task:raise ValueError('Task must belong to this project')
    root=Path(main).parent/'attachments'
    items=validate_metadata(json.loads(task['attachments']))
    if mode=='list':return {'items':[dict(item,available=blob_path(root,item['id']).is_file()) for item in items],'content_included':False}
    if mode in ('add','remove'):
        actor=actor_user(db,a);runtime.gate(db,project,actor['id']);lock_access(task,a)
    if mode=='add':
        name=filename(a.get('name'));encoded=a.get('content_base64')
        if len(items)>=MAX_FILES:raise ValueError('Maximum ten attachments per task')
        if not isinstance(encoded,str) or not 1<=len(encoded)<=4*((MAX_BYTES+2)//3):raise ValueError('Attachment content must be base64, at most 5 MiB decoded')
        try:data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error):raise ValueError('Invalid base64 attachment content') from None
        if not 1<=len(data)<=MAX_BYTES:raise ValueError('Attachment must contain 1 byte to 5 MiB')
        mime=TYPES[Path(name).suffix.lower()]
        if mime.startswith('image/'):image_size(data,mime)
        item=dict(id=uuid.uuid4().hex,name=name,mime=mime,size=len(data),sha256=hashlib.sha256(data).hexdigest(),created=now(),author=actor['identity'],previewable=mime.startswith('image/'))
        path=blob_path(root,item['id']);root.mkdir(exist_ok=True)
        created=False
        try:
            with path.open('xb') as output:
                created=True;output.write(data)
            db.execute('UPDATE tasks SET attachments=?,updated=? WHERE id=?',(json.dumps(items+[item]),now(),task['id']))
            activity(db,task['id'],actor['id'],'comment','Reference attached: '+name)
            db.commit()
        except Exception:
            if created and path.is_file() and not path.is_symlink():path.unlink()
            raise
        return dict(item,available=True)
    key=a.get('attachment_id')
    if not isinstance(key,str):raise ValueError('Attachment ID required')
    item=next((i for i in items if i['id']==key),None)
    if not item:raise ValueError('Attachment must belong to this task and project')
    path=blob_path(root,key)
    if mode=='remove':
        db.execute('UPDATE tasks SET attachments=?,updated=? WHERE id=?',(json.dumps([i for i in items if i['id']!=key]),now(),task['id']))
        activity(db,task['id'],actor['id'],'comment','Reference removed: '+item['name'])
        db.commit()
        try:path.unlink(missing_ok=True)
        except OSError:return {'removed':True,'warning':'Reference removed; local content cleanup failed. Inspect local attachment storage.'}
        return {'removed':True}
    result=dict(item,available=path.is_file())
    if a.get('include_content',False):
        if not path.is_file():raise ValueError('Reference content is missing. JSON exports contain metadata only; copy local attachment blobs separately.')
        if path.stat().st_size!=item['size']:raise ValueError('Reference content size does not match metadata')
        with path.open('rb') as source:data=source.read(MAX_BYTES+1)
        if len(data)!=item['size']:raise ValueError('Reference content size does not match metadata')
        if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Reference content digest does not match metadata')
        result['content_base64']=base64.b64encode(data).decode('ascii')
    return result
