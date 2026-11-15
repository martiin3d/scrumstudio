"""Check the repository wiki for broken local references and unindexed pages."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from wiki_knowledge import diagnose


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT,help='Checkout to inspect')
    args=parser.parse_args()
    issues=diagnose(args.root)
    print(json.dumps({'passed':not issues,'issues':issues},ensure_ascii=False))
    return int(bool(issues))


if __name__=='__main__':
    raise SystemExit(main())
