"""Small bounded JSONL client, shared by non-model bridge utilities."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]

def tool_timeout(name,args,default=20):
    # Completion may publish source and target (60 seconds each) after Done.
    # Leave room for commit/merge and bookkeeping without slowing ordinary calls.
    completion=name=='work_finish' or (name=='task_update' and args.get('status')=='Done')
    return 180 if completion else default

class ToolError(RuntimeError):
    def __init__(self,result):
        structured=result.get('structuredContent')
        self.detail=structured.get('error') if isinstance(structured,dict) else None
        if not isinstance(self.detail,dict):self.detail=None
        content=result.get('content',[])
        if not isinstance(content,list):content=[]
        text=next((item.get('text','') for item in content if isinstance(item,dict) and item.get('type')=='text'),'MCP tool failed')
        super().__init__(self.detail.get('message',text) if self.detail else text)


class JsonClient:
    def __init__(self,command):
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace')
        self.messages=queue.Queue()
        self.sequence=0
        threading.Thread(target=self._read,daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    value=json.loads(line)
                    if isinstance(value,dict):self.messages.put(value)
                except ValueError:
                    continue
        finally:
            self.messages.put(None)

    def send(self,message):
        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()

    def rpc(self,method,params=None,timeout=20):
        self.sequence+=1
        request={'jsonrpc':'2.0','id':self.sequence,'method':method}
        if params is not None:
            request['params']=params
        self.send(request)
        import time
        deadline=time.monotonic()+timeout
        while True:
            try:
                value=self.messages.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty:
                raise RuntimeError('Local JSON-RPC response timed out') from None
            if value is None:
                raise RuntimeError('Local JSON-RPC process closed')
            if value.get('id')==self.sequence:
                if 'error' in value:
                    raise RuntimeError('Local JSON-RPC request failed')
                if 'result' not in value:raise RuntimeError('Malformed local JSON-RPC response')
                return value['result']
            if time.monotonic()>deadline:
                raise RuntimeError('Local JSON-RPC response timed out')

    def close(self):
        try:
            if self.process.poll() is None:
                try:self.process.stdin.close()
                except OSError:pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill();self.process.wait(timeout=2)
        finally:
            for stream in (self.process.stdin,self.process.stdout):
                try:stream.close()
                except OSError:pass

class ScrumClient(JsonClient):
    def __init__(self,timeout=20):
        super().__init__([sys.executable,str(ROOT/'mcp_server.py')])
        try:
            self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'scrum-control-bridge','version':'1'}},timeout=timeout)
            self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        except Exception:
            self.close();raise

    def call(self,name,args,timeout=None):
        if timeout is None:timeout=tool_timeout(name,args)
        result=self.rpc('tools/call',{'name':name,'arguments':args},timeout=timeout)
        if not isinstance(result,dict):raise RuntimeError('Malformed MCP tool response')
        if result.get('isError'):
            raise ToolError(result)
        try:return json.loads(result['content'][0]['text'])
        except (KeyError,IndexError,TypeError,ValueError):
            raise RuntimeError('Malformed MCP tool content') from None


def main(argv=None):
    """One tool call through the real server; never replay an uncertain write."""
    import argparse
    import math
    import time

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool',required=True)
    parser.add_argument('--args-file',type=Path,help='UTF-8 JSON arguments; otherwise read JSON from stdin')
    parser.add_argument('--timeout',type=float,help='Explicit seconds per RPC (maximum 300); default: initialization/ordinary calls 15, completion writes 180')
    options=parser.parse_args(argv)
    if options.timeout is not None and (not math.isfinite(options.timeout) or not 0<options.timeout<=300):
        parser.error('--timeout must be greater than zero and at most 300 seconds')
    try:
        raw=options.args_file.read_text(encoding='utf-8-sig') if options.args_file else (sys.stdin.read() if not sys.stdin.isatty() else '{}')
        arguments=json.loads(raw.strip().lstrip('\ufeff') or '{}')
        if not isinstance(arguments,dict):
            raise ValueError('Tool arguments must be a JSON object')
    except (OSError,ValueError) as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr)
        return 2

    started=time.perf_counter()
    client=None
    try:
        client=ScrumClient(timeout=options.timeout if options.timeout is not None else 15)
        result=client.call(options.tool,arguments,timeout=options.timeout if options.timeout is not None else tool_timeout(options.tool,arguments,15))
        print(json.dumps(result,ensure_ascii=True))
        return 0
    except (RuntimeError,OSError,ValueError) as error:
        detail={'error':str(error),'retried':False,'next_step':'For an uncertain write, inspect current state before retrying; it may already have committed.'}
        if isinstance(error,ToolError) and error.detail:detail['diagnostic']=error.detail
        print(json.dumps(detail),file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
        print(json.dumps({'transport':'stdio','elapsed_ms':round((time.perf_counter()-started)*1000,2)}),file=sys.stderr)


if __name__=='__main__':
    raise SystemExit(main())
