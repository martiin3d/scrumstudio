"""Plan proportional checks; --run executes the selected checks once."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import hashlib
import shutil
import time

ROOT=Path(__file__).resolve().parents[1]
TARGETS={
    'workflow.py':['test_workflow.py','test_efficiency.py'],
    'context_tools.py':['test_context_tools.py','test_efficiency.py'],
    'overhead.py':['test_overhead.py','test_efficiency.py'],
    'telemetry.py':['test_telemetry.py'],
    'scripts/setup.py':['test_setup.py'],
    'scripts/agent_bridge.py':['test_bridge.py'],
    'scripts/provider_usage.py':['test_bridge.py','test_overhead.py'],
    'scripts/usage_monitor.py':['test_overhead.py'],
}

IGNORED={'.git','.scrum','data','__pycache__','node_modules','.venv','.pytest_cache'}

def fingerprint(root=ROOT):
    """Conservative tree hash; unavailable/oversized inputs disable reuse."""
    digest=hashlib.sha256();count=total=0
    def fail(error):raise error
    try:
        for folder,dirs,files in os.walk(root,onerror=fail):
            dirs[:]=sorted(d for d in dirs if d not in IGNORED)
            if any((Path(folder)/d).is_symlink() for d in dirs):return None
            for name in sorted(files):
                path=Path(folder)/name
                if path.is_symlink():return None
                count+=1;total+=path.stat().st_size
                if count>5000 or total>50*1024*1024:return None
                digest.update(path.relative_to(root).as_posix().encode());digest.update(b'\0')
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()
    except OSError:return None

def signature(selected,source):
    node=shutil.which('node') if selected['node_syntax'] else None
    try:node_stamp=Path(node).stat().st_mtime_ns if node else None
    except OSError:node_stamp=None
    value=[source,sys.version,sys.executable,node,node_stamp,selected['tests'],selected['node_syntax']]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()

def read_cached(path,key):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if value.get('signature')==key and value.get('passed') is True else None
    except (OSError,ValueError,AttributeError):return None

def save_pass(path,key,source):
    import tempfile
    path.parent.mkdir(parents=True,exist_ok=True)
    data={'signature':key,'source':source,'passed':True,'verified_at':time.time()}
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as saved:
        json.dump(data,saved);temporary=saved.name
    os.replace(temporary,path)


def plan(files):
    checks=set();notes=[];full=False;js=False
    for value in files:
        path=(ROOT/value).resolve()
        try:name=path.relative_to(ROOT).as_posix()
        except ValueError:raise ValueError('Files must be inside this checkout') from None
        if name=='public/app.js':js=True;notes.append('Verify the affected web flow in a browser.')
        elif name.startswith('tests/test_') and name.endswith('.py'):checks.add(Path(name).name)
        elif name in TARGETS:checks.update(TARGETS[name])
        elif name.endswith('.md') or name in ('public/style.css','public/index.html'):
            notes.append('Inspect documentation/skill references or the affected visual flow; no model-output matching tests.')
        else:full=True
    return {'tests':['test_*.py'] if full else sorted(checks),'node_syntax':js,'notes':sorted(set(notes)),
            'reason':'Cross-cutting or unmapped change: run full regressions.' if full else 'Targeted checks; expand for new failures, broader impact or unresolved risk.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files',nargs='+',required=True)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--reuse',action='store_true',help='Reuse an actual prior pass only for identical sources, runtime and checks; report its original time')
    args=parser.parse_args();selected=plan(args.files)
    print(json.dumps(selected),flush=True)
    if not args.run:return
    source=fingerprint()
    key=signature(selected,source)
    cache=ROOT/'data'/'verification-cache'/('check-'+hashlib.sha256(json.dumps([selected['tests'],selected['node_syntax']]).encode()).hexdigest()[:20]+'.json')
    cached=read_cached(cache,key) if args.reuse and source else None
    if cached:
        print(json.dumps({'reused':True,'original_verified_at':cached['verified_at'],'source':source,'checks':selected['tests'],'node_syntax':selected['node_syntax']}),flush=True)
        return
    os.chdir(ROOT);sys.path.insert(0,str(ROOT/'tests'));sys.path.insert(0,str(ROOT))
    suite=unittest.TestSuite()
    for pattern in selected['tests']:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern))
    passed=True
    if selected['tests']:
        passed=unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
    if selected['node_syntax']:
        passed=subprocess.run(['node','--check','public/app.js'],check=False).returncode==0 and passed
    if passed and source and (selected['tests'] or selected['node_syntax']) and fingerprint()==source:
        try:save_pass(cache,key,source)
        except OSError:print('Verification passed; cache unavailable.',file=sys.stderr)
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()
