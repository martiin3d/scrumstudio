"""Bounded installed-Editor test runner; never installs or changes licensing.

Run through the caller's normal reviewed tool permissions when Unity needs
per-user licensing IPC. This helper grants no permissions and never retries.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET


def classify(log, report, exit_code, timed_out=False):
    # A license reconnect warning is not fatal if compilation/tests progressed.
    errors=list(dict.fromkeys(re.findall(r'^.*error CS\d+[^\r\n]*',log,re.M)))
    result={'passed':False,'exit_code':exit_code,'timed_out':timed_out}
    if errors:
        return dict(result,kind='compile_error',diagnostics=errors[:12],next_step='Repair the reported project compiler errors; do not diagnose a missing license.')
    if report is not None:
        try:
            root=ET.fromstring(report)
            total=int(root.get('total',root.get('testcasecount','0')))
            executed=int(root.get('passed','0'))+int(root.get('failed','0'))
            passed=exit_code==0 and not timed_out and total>0 and executed>0 and root.get('result')=='Passed' and int(root.get('failed','0'))==0
            return dict(result,passed=passed,kind='passed' if passed else 'test_failure',tests=total,executed=executed,result=root.get('result'))
        except (ET.ParseError,ValueError):
            return dict(result,kind='invalid_report',next_step='Inspect the fresh test report; no pass is established.')
    if re.search(r'UnauthorizedAccessException|Access.*denied|Acceso denegado|CurlRequestCache|Failed to (?:connect|handshake) to channel|Licensing initialization failed|Channel doesn.t exist',log,re.I):
        return dict(result,kind='environment_access',next_step='Request normal reviewed execution of this exact command in the current signed-in user session, once. Do not repeat sandboxed execution, change licensing, or weaken global permissions. If reviewed execution also fails, preserve the concrete diagnostic and stop bounded recovery.')
    return dict(result,kind='timeout' if timed_out else 'missing_report',next_step='Inspect the current log; no executed test report means acceptance is unverified.')


def run(editor, project, platform='EditMode', test_filter='', timeout=300):
    editor=Path(editor).resolve(strict=True); project=Path(project).resolve(strict=True)
    if not (project/'ProjectSettings/ProjectVersion.txt').is_file():
        raise ValueError('Project must be the Unity root containing ProjectSettings/ProjectVersion.txt')
    if editor.name.lower() not in ('unity.exe','unity'):
        raise ValueError('Editor must be an installed Unity executable')
    logs=project/'Logs'; logs.mkdir(exist_ok=True)
    # Unique paths cannot accidentally reuse a previous passing report.
    stem='Scrum-verify-'+uuid.uuid4().hex
    log=logs/(stem+'.log'); report=logs/(stem+'.xml')
    cmd=[str(editor),'-batchmode','-nographics','-projectPath',str(project),'-runTests','-testPlatform',platform,'-testResults',str(report),'-logFile',str(log)]
    if test_filter:cmd+=['-testFilter',test_filter]
    process=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    timed_out=False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True
        # Terminate only the exact Editor we started; never kill shared licensing
        # clients, Hub, other Editors or arbitrary process-name matches.
        process.kill(); process.wait(timeout=15)
    result=classify(log.read_text(encoding='utf-8',errors='replace') if log.exists() else '',report.read_text(encoding='utf-8-sig') if report.exists() else None,process.returncode,timed_out)
    return dict(result,log=str(log),report=str(report))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--platform',choices=['EditMode','PlayMode'],default='EditMode')
    parser.add_argument('--filter',default='')
    parser.add_argument('--timeout',type=int,default=300)
    args=parser.parse_args()
    if not 30<=args.timeout<=1800:parser.error('timeout must be 30..1800 seconds')
    try:result=run(args.editor,args.project,args.platform,args.filter,args.timeout)
    except (OSError,ValueError) as error:result={'passed':False,'kind':'launch_error','error':str(error),'next_step':'For access denial request the normal tool permission review; never claim tests passed.'}
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
