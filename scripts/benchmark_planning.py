"""Equivalent isolated MCP planning writes; bytes/time are not provider tokens."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_storage import StorageTest
from mcp_server import handle,TOOLS


def measure(batch):
    fixture=StorageTest();fixture.setUp()
    try:
        project,_=fixture.project()
        items=[dict(key=key,title=title,description='Deliver '+title,acceptance_criteria=[title+' is independently verified'],files=[key+'.py']) for key,title in [('address','Validate delivery address'),('receipt','Download purchase receipt'),('locale','Display localized order totals')]]
        calls=[('task_plan',dict(project=project,actor=fixture.agent,items=items))] if batch else [('task_create',dict(project=project,actor=fixture.agent,**{k:v for k,v in item.items() if k!='key'})) for item in items]
        request_bytes=response_bytes=0;elapsed=0
        for number,(name,args) in enumerate(calls,1):
            request=dict(jsonrpc='2.0',id=number,method='tools/call',params=dict(name=name,arguments=args))
            started=time.perf_counter();response=handle(request);elapsed+=(time.perf_counter()-started)*1000
            if response['result'].get('isError'):raise RuntimeError(response['result'])
            request_bytes+=len(json.dumps(request,separators=(',',':')).encode())
            response_bytes+=len(json.dumps(response,separators=(',',':')).encode())
        return dict(mcp_calls=len(calls),request_bytes=request_bytes,response_bytes=response_bytes,server_ms=round(elapsed,3))
    finally:fixture.tearDown()


if __name__=='__main__':
    declaration=next(dict(name=n,description=d,inputSchema=s) for n,d,s in TOOLS if n=='task_plan')
    print(json.dumps(dict(baseline=measure(False),batched=measure(True),added_tool_declaration_bytes=len(json.dumps(declaration,separators=(',',':')).encode()),note='Three equivalent independent TODO outcomes via actual MCP handlers on isolated data. No model/worker calls; excludes client transport latency. The new declaration adds initial tool-catalog bytes. Bytes and server milliseconds do not measure provider tokens or guarantee zero quota cost.')))
