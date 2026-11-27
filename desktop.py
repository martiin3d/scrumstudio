"""Launch Scrum Studio in a Windows WebView2 window."""

import argparse
import json
import logging
import os
import subprocess
import sys
from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Thread

from server import Handler, LocalHTTPServer

DATA = Path(__file__).resolve().parent / 'data'
APP_ID = 'ScrumStudio.Desktop'
TOAST_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$payload = $input | ConvertFrom-Json
$manager = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]
$xml = $manager::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$lines = $xml.GetElementsByTagName('text')
$lines.Item(0).InnerText = $payload.title
$lines.Item(1).InnerText = $payload.message
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$manager::CreateToastNotifier($payload.app_id).Show($toast)
'''


def show_desktop_toast(icon_path, title, message):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\AppUserModelId\{APP_ID}') as key:
        winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'Scrum Studio')
        if icon_path.is_file():
            winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon_path))

    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run(
        [str(powershell), '-NoProfile', '-NonInteractive', '-Command', TOAST_SCRIPT],
        input=json.dumps({'app_id': APP_ID, 'title': title, 'message': message}).encode('ascii'),
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
    )
    if result.returncode:
        raise OSError(result.stderr.decode(errors='replace').strip())


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


class DesktopNotifications:
    def __init__(self, icon_path):
        self.icon_path = icon_path
        self.pending = deque()
        self.window = None

    def attach(self, window):
        self.window = window
        window.events.before_show += self._start

    def _start(self):
        from System.Windows.Forms import Timer

        self.poll = Timer()
        self.poll.Interval = 15000
        self.poll.Tick += self._refresh
        self.delivery = Timer()
        self.delivery.Interval = 10000
        self.delivery.Tick += self._deliver
        self.window.native.FormClosed += self._close
        self.poll.Start()

    def _refresh(self, sender, args):
        # Run on the UI thread without waiting for WebView2 to return a result.
        view = self.window.native.browser.webview
        if view.CoreWebView2 is not None:
            view.ExecuteScriptAsync('globalThis.refreshVisiblePage?.()')

    def notify(self, title, message):
        from System import Action

        if not isinstance(title, str) or not isinstance(message, str):
            return

        def enqueue():
            self.pending.append((title[:63], message[:255]))
            if not self.delivery.Enabled:
                self._deliver(None, None)
                self.delivery.Start()

        self.window.native.BeginInvoke(Action(enqueue))

    def _deliver(self, sender, args):
        if not self.pending:
            self.delivery.Stop()
            return
        title, message = self.pending.popleft()
        Thread(target=self._show, args=(title, message), daemon=True).start()

    def _show(self, title, message):
        try:
            show_desktop_toast(self.icon_path, title, message)
        except (OSError, subprocess.TimeoutExpired):
            logging.exception('Could not show a Scrum Studio desktop notification')

    def _close(self, sender, args):
        self.poll.Dispose()
        self.delivery.Dispose()


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
            notifications = DesktopNotifications(icon)
            window = webview.create_window(
                'Scrum Studio Desktop', f'{url}/?desktop=1',
                width=1440, height=900, min_size=(1080, 680), text_select=True,
            )
            window.expose(notifications.notify)
            notifications.attach(window)
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
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
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
