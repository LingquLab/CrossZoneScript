# Copyright (c) 2026 TileXR Project.
"""Run a scoped simulator recovery action and retain all verbose output in green."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',required=True,type=Path)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args)=={'action'} and args['action'] in ('stop-joint','snapshot-send','read-cq','snapshot-joint','read-joint-trace','read-joint-waits','read-joint-mte','snapshot-joint-state','snapshot-joint-early')
    here=Path(__file__).parent
    args['snapshot_script']=(here/'snapshot_case.py').read_text()
    if args['action']=='read-cq':
        args['snapshot_script']=(here/'cq_read_case.py').read_text()
    if args['action']=='snapshot-joint':
        args['snapshot_script']=(here/'joint_snapshot_case.py').read_text()
    if args['action']=='read-joint-trace':
        args['snapshot_script']=(here/'joint_trace_read_case.py').read_text()
    if args['action']=='read-joint-waits':
        args['snapshot_script']=(here/'joint_wait_read_case.py').read_text()
    if args['action']=='read-joint-mte':
        args['snapshot_script']=(here/'joint_mte_read_case.py').read_text()
    if args['action']=='snapshot-joint-state':
        args['snapshot_script']=(here/'joint_state_snapshot_case.py').read_text()
    if args['action']=='snapshot-joint-early':
        args['snapshot_script']=(here/'joint_early_snapshot_case.py').read_text()
    from sim_green import SCRIPT_FILES
    files={name:(here/name).read_text(encoding='utf-8') for name in SCRIPT_FILES}
    args['tool_key']=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()[:16]
    code=(here/'recovery_host.py').read_text()+'\nraise SystemExit(main('+repr(args)+'))\n'
    budget=2150 if args['action']=='snapshot-joint' else (1470 if args['action'] in ('snapshot-send','snapshot-joint-state') else 65)
    if args['action']=='snapshot-joint-early':budget=1170
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    cmd=['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
         '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','-o','ServerAliveInterval=20',
         '-o','ServerAliveCountMax=3','root@141.61.94.26',f'timeout --kill-after=20 {budget} python3 -']
    p=subprocess.run(cmd,input=code.encode(),capture_output=True,timeout=budget+40)
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/59af0a93-b62f-4211-927e-a44f047667cf'
    (logs/(args['action']+'.stdout')).write_bytes(p.stdout)
    (logs/(args['action']+'.stderr')).write_bytes(p.stderr)
    if p.returncode==0:
        assert len(p.stdout)<=4096
        print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else:
        print(json.dumps(dict(action=args['action'],exit_code=p.returncode,evidence='green_local_logs')))
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
