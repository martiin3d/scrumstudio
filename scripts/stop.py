"""Stop only this checkout's managed website; Windows/macOS entrypoint."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from web_lifecycle import stop

if __name__=='__main__':
    try:
        raise SystemExit(stop(int(os.environ.get('SCRUM_PORT','8765'))))
    except (ValueError,OSError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
