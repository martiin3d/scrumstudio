"""Deterministic screenshot diagnostics. No model call or semantic fidelity claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from PIL import Image,ImageChops


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def compare(reference,actual,output):
    reference=Path(reference).resolve();actual=Path(actual).resolve();output=Path(output).resolve()
    targets=[output/name for name in ('overlay.png','diff.png','comparison.json')]
    if any(p.is_symlink() or p.resolve() in (reference,actual) or (p.exists() and any(os.path.samefile(p,x) for x in (reference,actual))) for p in targets):raise ValueError('Output must not overwrite either input')
    for path in (reference,actual):
        if path.stat().st_size>64*1024*1024:raise ValueError('Input exceeds 64 MiB')
    inputs={'reference':digest(reference),'actual':digest(actual)}
    if targets[2].is_file():
        try:old=json.loads(targets[2].read_text(encoding='utf-8'))
        except (OSError,ValueError):old={}
        if not isinstance(old,dict):old={}
        if old.get('algorithm')==1 and old.get('input_sha256')==inputs and all(path.is_file() and old.get('output_sha256',{}).get(path.name)==digest(path) for path in targets[:2]):return old|{'cached':True}
    with Image.open(reference) as source,Image.open(actual) as target:
        if source.size!=target.size:raise ValueError('Reference and actual dimensions differ; capture matching dimensions instead of stretching')
        if source.width*source.height>16000000:raise ValueError('Comparison exceeds 16 million pixels')
        source=source.convert('RGBA');target=target.convert('RGBA');difference=ImageChops.difference(source,target)
        channels=difference.split();mask=channels[0]
        for channel in channels[1:]:mask=ImageChops.lighter(mask,channel)
        histogram=mask.histogram();different=source.width*source.height-histogram[0]
        output.mkdir(parents=True,exist_ok=True)
        Image.blend(source,target,.5).save(targets[0]);difference.convert('RGB').save(targets[1])
        report={'algorithm':1,'input_sha256':inputs,'reference_name':reference.name,'actual_name':actual.name,'size':list(source.size),'pixels_different':different,'difference_bounds':mask.getbbox(),'pixel_identical':different==0,'output_sha256':{p.name:digest(p) for p in targets[:2]},'cached':False,'note':'Raw pixel diagnostics, not a semantic Figma fidelity percentage or human approval. Inspect material differences; do not automatically complete tasks from this report.'}
        targets[2].write_text(json.dumps(report,indent=2),encoding='utf-8');return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('reference','actual','output'):p.add_argument('--'+name,required=True)
    args=p.parse_args()
    try:print(json.dumps(compare(args.reference,args.actual,args.output)));return 0
    except (OSError,ValueError) as error:print(json.dumps({'error':str(error)}));return 1

if __name__=='__main__':raise SystemExit(main())
