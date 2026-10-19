import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from store import run
from diagnostics import failure

PUBLIC = Path(__file__).resolve().parent / 'public'

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/projects':
            try:
                return self.reply(200, run('projects', {}))
            except Exception as error:
                detail=failure(error,'projects')
                return self.reply(500,{'error':detail['message'],'diagnostic':detail})
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        if path not in files:
            return self.reply(404, {'error': 'Not found'})
        name, mime = files[path]
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.end_headers()
        self.wfile.write((PUBLIC / name).read_bytes())

    def do_POST(self):
        # Loopback-only service; reject cross-origin browser mutations.
        if self.headers.get('Origin') not in [None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}']:
            return self.reply(403, {'error': 'Origin rejected'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'JSON required'})
        try:
            action = urlparse(self.path).path.removeprefix('/api/')
            length = int(self.headers.get('Content-Length', 0))
            if length < 1 or length > (7_100_000 if action=='task_attachment' else 100000):
                raise ValueError('Invalid request size')
            payload=json.loads(self.rfile.read(length))
            if action=='_shutdown':
                token=payload.get('token') if isinstance(payload,dict) else None
                expected=getattr(self.server,'shutdown_token',None)
                if not expected or not isinstance(token,str) or not secrets.compare_digest(token,expected):
                    return self.reply(403,{'error':'Shutdown rejected'})
                self.reply(200,{'stopping':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if action=='web_refresh':
                import web_refresh
                result=web_refresh.refresh(payload,run)
            else:result = run(action, payload)
            self.reply(200, result)
        except (ValueError, KeyError, TypeError) as e:
            self.reply(400, {'error': str(e)})
        except Exception as error:
            detail=failure(error,'web_request')
            self.reply(500, {'error':detail['message'],'diagnostic':detail})

    def reply(self, code, value):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

if __name__ == '__main__':
    port = int(os.environ.get('SCRUM_PORT', '8765'))
    from web_lifecycle import publish,clear
    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        server.daemon_threads=False
        server.shutdown_token=publish(port)
        print(f'Scrum Studio: http://127.0.0.1:{port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            clear(port,server.shutdown_token)
