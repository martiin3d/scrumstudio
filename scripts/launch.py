"""Launch the local website and open it when ready."""
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser
def main():
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required.')
    root = Path(__file__).resolve().parents[1]
    port = int(os.environ.get('SCRUM_PORT', '8765'))
    url = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, str(root / 'server.py')], cwd=root)
    try:
        for _ in range(50):
            if process.poll() is not None:
                return process.returncode
            try:
                with urllib.request.urlopen(url + '/api/projects', timeout=.2) as response:
                    if process.poll() is None and response.status == 200:
                        webbrowser.open(url)
                        break
            except OSError:
                time.sleep(.1)
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        return 0
if __name__ == '__main__':
    sys.exit(main())
