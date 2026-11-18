"""Launch Scrum Studio in a Windows WebView2 window."""

import argparse
import os
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Thread

from server import Handler, LocalHTTPServer

DATA = Path(__file__).resolve().parent / 'data'


@contextmanager
def desktop_backend(port):
    with LocalHTTPServer(('127.0.0.1', port), Handler) as server:
        server.daemon_threads = False
        thread = Thread(target=server.serve_forever, name='scrum-desktop-http', daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            server.shutdown()
            thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=os.environ.get('SCRUM_DESKTOP_PORT', '8766'))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if sys.platform != 'win32':
        parser.error('Scrum Studio Desktop currently requires Windows')
    try:
        import webview
    except ImportError as error:
        parser.exit(1, f'Unable to load the desktop runtime ({error}). See the Desktop setup in README.md.\n')

    profile = Path(os.environ.get('SCRUM_DESKTOP_PROFILE', DATA / 'desktop-profile'))
    icon = DATA / 'desktop' / 'Scrum Studio.ico'
    if not icon.is_file():
        icon = Path(sys.executable).resolve().parent.parent / 'Scrum Studio.ico'
    options = {'icon': str(icon)} if icon.is_file() else {}
    try:
        with desktop_backend(args.port) as url:
            webview.settings['ALLOW_DOWNLOADS'] = True
            webview.create_window(
                'Scrum Studio Desktop', f'{url}/?desktop=1',
                width=1440, height=900, min_size=(1080, 680), text_select=True,
            )
            webview.start(gui='edgechromium', private_mode=False, storage_path=str(profile), **options)
    except OSError as error:
        parser.exit(1, f'Could not start Scrum Studio Desktop ({error}). Try a free --port if the port is in use.\n')


def run():
    if sys.stderr is not None:
        return main()

    import ctypes
    import traceback

    app_dir = DATA / 'desktop'
    log_path = app_dir / 'desktop.log'
    try:
        app_dir.mkdir(parents=True, exist_ok=True)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ScrumStudio.Desktop')
        # Background worker commands still need the console interpreter for stdio.
        sys.executable = str(Path(sys.executable).with_name('python.exe'))
        with log_path.open('a', encoding='utf-8', buffering=1) as log, redirect_stderr(log), redirect_stdout(log):
            try:
                return main()
            except Exception:
                traceback.print_exc()
                raise
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and not error.code:
            return
        ctypes.windll.user32.MessageBoxW(
            None, f'Scrum Studio could not start. See the local log at\n{log_path}',
            'Scrum Studio Desktop', 0x10,
        )
        raise SystemExit(1)


if __name__ == '__main__':
    run()
