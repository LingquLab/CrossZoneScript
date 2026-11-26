# Copyright (c) 2026 TileXR Project.
"""Fetch a small read-only diagnostic packet over the existing green SSH account."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',required=True,type=Path)
    assert json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))=={}
    here=Path(__file__).parent
    code=(here/'snapshot_evidence_host.py').read_text()+'\nraise SystemExit(main())\n'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    cmd=['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
         '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
         'timeout --kill-after=5 60 python3 -']
    p=subprocess.run(cmd,input=code.encode(),capture_output=True,timeout=80)
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/59af0a93-b62f-4211-927e-a44f047667cf'
    logs.mkdir(parents=True,exist_ok=True)
    (logs/'snapshot-evidence.stdout').write_bytes(p.stdout)
    (logs/'snapshot-evidence.stderr').write_bytes(p.stderr)
    if p.returncode==0:
        assert len(p.stdout)<=4096
        print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else:
        print(json.dumps(dict(diagnostic_exit=p.returncode,evidence='green_local_logs')))
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
