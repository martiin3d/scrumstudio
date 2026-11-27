"""Launch Scrum Studio in a Windows WebView2 window."""

import argparse
import ctypes
import json
import logging
import os
import re
import subprocess
import sys
from collections import deque
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from multiprocessing.connection import Client, Listener
from pathlib import Path
from threading import Thread
from time import monotonic
from uuid import uuid4

DATA = Path(__file__).resolve().parent / 'data'
APP_ID = 'ScrumStudio.Desktop'
TOAST_PROTOCOL = 'scrum-studio-toast'
TOAST_LIFETIME = 24 * 60 * 60
TOAST_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$payload = $input | ConvertFrom-Json
$manager = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]
$xml = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime]::new()
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text/><text/></binding></visual></toast>')
$xml.DocumentElement.SetAttribute('activationType', 'protocol')
$xml.DocumentElement.SetAttribute('launch', $payload.activation)
$lines = $xml.GetElementsByTagName('text')
$lines.Item(0).InnerText = $payload.title
$lines.Item(1).InnerText = $payload.message
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.ExpirationTime = [DateTimeOffset]::Now.AddSeconds($payload.lifetime)
$manager::CreateToastNotifier($payload.app_id).Show($toast)
'''


def toast_pipe(instance):
    return rf'\\.\pipe\ScrumStudio.Toast.{instance}'


def activate_desktop_toast(uri):
    match = re.fullmatch(rf'{TOAST_PROTOCOL}://([0-9a-f]{{32}})/([0-9a-f]{{32}})/?', uri)
    if not match:
        return
    instance, ticket = match.groups()
    try:
        with Client(toast_pipe(instance), family='AF_PIPE') as connection:
            # The shell-launched handler can hand its foreground privilege to the running app.
            pid = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetNamedPipeServerProcessId(
                ctypes.c_void_p(connection.fileno()), ctypes.byref(pid),
            ):
                ctypes.windll.user32.AllowSetForegroundWindow(pid.value)
            connection.send_bytes(bytes.fromhex(ticket))
    except OSError:
        # The original window may have closed since the toast was displayed.
        return


def show_desktop_toast(icon_path, title, message, activation):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\AppUserModelId\{APP_ID}') as key:
        winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'Scrum Studio')
        if icon_path.is_file():
            winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon_path))

    pythonw = Path(sys.executable).with_name('pythonw.exe')
    command = f'"{pythonw if pythonw.is_file() else sys.executable}" "{Path(__file__).resolve()}" --activate-toast "%1"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\{TOAST_PROTOCOL}') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, 'URL:Scrum Studio notification')
        winreg.SetValueEx(key, 'URL Protocol', 0, winreg.REG_SZ, '')
        with winreg.CreateKey(key, r'shell\open\command') as shell:
            winreg.SetValueEx(shell, '', 0, winreg.REG_SZ, command)

    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run(
        [str(powershell), '-NoProfile', '-NonInteractive', '-Command', TOAST_SCRIPT],
        input=json.dumps({
            'app_id': APP_ID, 'title': title, 'message': message,
            'activation': activation, 'lifetime': TOAST_LIFETIME,
        }).encode('ascii'),
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
    )
    if result.returncode:
        raise OSError(result.stderr.decode(errors='replace').strip())


@contextmanager
def desktop_backend(port):
    from server import Handler, LocalHTTPServer

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
        self.instance = uuid4().hex
        self.destinations = {}

    def attach(self, window):
        self.window = window
        window.events.before_show += self._start

    def _start(self):
        from System.Windows.Forms import Timer

        self.listener = Listener(toast_pipe(self.instance), family='AF_PIPE')
        Thread(target=self._listen, daemon=True).start()
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

    def notify(self, title, message, project=None, view='Board', question=None):
        from System import Action

        if not isinstance(title, str) or not isinstance(message, str):
            return
        if project is not None and (type(project) is not int or project <= 0):
            return
        if view not in ('Board', 'Chat') or (question is not None and not isinstance(question, str)):
            return

        def enqueue():
            self.pending.append((title[:63], message[:255], (project, view, question)))
            if not self.delivery.Enabled:
                self._deliver(None, None)
                self.delivery.Start()

        self.window.native.BeginInvoke(Action(enqueue))

    def _deliver(self, sender, args):
        if not self.pending:
            self.delivery.Stop()
            return
        title, message, destination = self.pending.popleft()
        now = monotonic()
        self.destinations = {key: value for key, value in self.destinations.items() if value[0] > now}
        ticket = uuid4().hex
        self.destinations[ticket] = (now + TOAST_LIFETIME + 10, destination)
        activation = f'{TOAST_PROTOCOL}://{self.instance}/{ticket}'
        Thread(target=self._show, args=(title, message, activation), daemon=True).start()

    def _show(self, title, message, activation):
        try:
            show_desktop_toast(self.icon_path, title, message, activation)
        except (OSError, subprocess.TimeoutExpired):
            logging.exception('Could not show a Scrum Studio desktop notification')

    def _listen(self):
        from System import Action

        while True:
            try:
                connection = self.listener.accept()
            except (OSError, ValueError):
                return
            with connection:
                try:
                    if not connection.poll(1):
                        continue
                    ticket = connection.recv_bytes(16).hex()
                except (OSError, EOFError):
                    continue
            native = self.window.native
            if native is None or native.IsDisposed:
                return
            native.BeginInvoke(Action(lambda ticket=ticket: self._activate(ticket)))

    def _activate(self, ticket):
        from System.Windows.Forms import FormWindowState

        match = self.destinations.pop(ticket, None)
        if match is None or match[0] <= monotonic():
            return
        native = self.window.native
        native.WindowState = FormWindowState.Maximized
        ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(native.Handle.ToInt64()))
        native.Activate()
        view = native.browser.webview
        if view.CoreWebView2 is not None:
            args = ','.join(json.dumps(value) for value in match[1])
            view.ExecuteScriptAsync(f'globalThis.openDesktopNotification?.({args})')

    def _close(self, sender, args):
        self.listener.close()
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
    if len(sys.argv) == 3 and sys.argv[1] == '--activate-toast':
        activate_desktop_toast(sys.argv[2])
        return
    if sys.stderr is not None:
        return main()

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
