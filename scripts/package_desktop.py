"""Build the Windows x64 portable ZIP from this source checkout."""

import argparse
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlretrieve
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.14.10'
PYTHON_SHA256 = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
DEPENDENCIES = (
    'pywebview==6.2.1', 'pythonnet==3.2.1', 'clr_loader==0.3.1',
    'cffi==2.1.1', 'pycparser==3.12', 'typing_extensions==4.16.0',
    'bottle==0.13.4',
)
SOURCE_ONLY = 'proxy_tools==0.1.0'
PORTABLE_GUIDE = '''Scrum Studio Desktop for Windows x64

Extract the entire ZIP into a writable folder and double-click Scrum Studio.exe.
Keep the app and runtime folders beside the executable. Python is bundled.
Windows requires Microsoft Edge WebView2 Runtime:
https://developer.microsoft.com/microsoft-edge/webview2/

Open or create projects using Project folders. The registry and desktop profile
are stored inside app/data. Projects keep their data in their own .scrum folders.
Back up app/data before upgrading. Start the new version from a fresh folder and
copy app/data into it while both copies of the application are closed.

To run workers, install and sign in to Codex or Claude CLI separately. Git-based
task delivery requires Git. Bundled source and license notices are in app and runtime.
'''


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_application(destination):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    folders = {'public', 'scripts', 'skills'}
    root_files = {'LICENSE', 'README.md', 'requirements-desktop.txt', 'mcp-config.example.json'}
    build_only = {'scripts/ScrumStudioLauncher.cs', 'scripts/build_desktop.ps1', 'scripts/package_desktop.py'}
    for name in filter(None, tracked):
        if name in build_only:
            continue
        path = Path(name)
        include = (path.parts[0] in folders or path.parts[:2] == ('docs', 'agents') or
                   (len(path.parts) == 1 and (path.suffix == '.py' or name in root_files)))
        if include:
            source = ROOT / path
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise ValueError(f'Cannot bundle a linked path: {path}')
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def install_runtime(runtime, staging):
    python_zip = staging / 'python-embed.zip'
    url = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
    urlretrieve(url, python_zip)
    if sha256(python_zip) != PYTHON_SHA256:
        raise ValueError('Python download did not match the published SHA-256 checksum')
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(runtime)
    configuration, = runtime.glob('python*._pth')
    prefix = configuration.stem
    site_packages = runtime / 'Lib' / 'site-packages'
    site_packages.mkdir(parents=True)
    configuration.write_text(f'{prefix}.zip\n.\nLib/site-packages\n../app\n', encoding='ascii')
    pip = [sys.executable, '-m', 'pip', '--disable-pip-version-check']
    wheels = staging / 'wheels'
    # The embeddable interpreter intentionally ignores pip build isolation paths.
    # Build the pure Python source-only dependency with the host interpreter.
    subprocess.run([*pip, 'wheel', '--no-deps', '--wheel-dir', str(wheels), SOURCE_ONLY], check=True)
    subprocess.run([*pip, '--python', str(runtime / 'python.exe'), 'install', '--no-compile',
                    '--only-binary=:all:', '--find-links', str(wheels), '--target', str(site_packages),
                    *DEPENDENCIES, SOURCE_ONLY], check=True)


def build(version, output):
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if not powershell:
        raise RuntimeError('PowerShell is required to build the launcher')
    output.mkdir(parents=True, exist_ok=True)
    filename = f'Scrum-Studio-Desktop-{version}-windows-x64.zip'
    artifact = output / filename
    with tempfile.TemporaryDirectory(prefix='portable-build-', dir=output) as temporary:
        staging = Path(temporary)
        package = staging / 'Scrum Studio Desktop'
        app = package / 'app'
        runtime = package / 'runtime'
        copy_application(app)
        install_runtime(runtime, staging)
        subprocess.run([powershell, '-NoProfile', '-File', str(ROOT / 'scripts' / 'build_desktop.ps1'),
                        '-OutputDirectory', str(package)], check=True)
        (package / 'START HERE.txt').write_text(PORTABLE_GUIDE, encoding='utf-8')
        subprocess.run([str(runtime / 'python.exe'), '-B', '-c',
                        'import sqlite3, webview, desktop; from scripts import cli_runtime'], cwd=package, check=True)
        draft = staging / filename
        with zipfile.ZipFile(draft, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(staging))
        os.replace(draft, artifact)
    checksum = artifact.with_suffix('.zip.sha256')
    checksum.write_text(f'{sha256(artifact)}  {artifact.name}\n', encoding='ascii')
    print(artifact)
    print(checksum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='Version in MAJOR.MINOR.PATCH form')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'releases')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().upper() not in ('AMD64', 'X86_64') or sys.version_info < (3, 11):
        parser.error('Build on Windows x64 with Python 3.11+ and pip')
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Version must have the form MAJOR.MINOR.PATCH')
    build(args.version, args.output.resolve())


if __name__ == '__main__':
    main()
