# Copyright (c) 2026 TileXR Project.
"""Run one pinned rank-send case through the green SSH account, retaining logs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import traceback

SESSION='59af0a93-b62f-4211-927e-a44f047667cf'
SCRIPT_FILES=('fixture.py','prefill.cpp','sim_case.py','return_case.py','addressed_exports.py',
              'profile_case.py','clock_evidence.json')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args)<={'action','tokens','attempt','timeout','candidate'}
    action=args['action']
    assert action in ('status','inter-server-send','joint-reuse')
    tokens,attempt,timeout=args.get('tokens',32),args.get('attempt',1),args.get('timeout',2400)
    assert tokens in (32,128) and type(attempt) is int and 1<=attempt<=20
    assert type(timeout) is int and 30<=timeout<=7200
    assert args.get('candidate','71b62db') in ('71b62db','2dfd2b4','b61cb53')
    args.update(tokens=tokens,attempt=attempt,timeout=timeout)
    here=Path(__file__).parent
    args['files']={name:(here/name).read_text(encoding='utf-8') for name in SCRIPT_FILES}
    remote_seconds=timeout+({32:500,128:1050}[tokens] if action!='status' else 20)
    code=(here/'sim_host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(args)+'))\n'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    cmd=['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
         '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','-o','ServerAliveInterval=20',
         '-o','ServerAliveCountMax=3','root@141.61.94.26',f'timeout --kill-after=30 {remote_seconds} python3 -']
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr'/SESSION/args.get('candidate','71b62db')
    logs.mkdir(parents=True,exist_ok=True)
    stem=logs/f'{action}-t{tokens}-a{attempt}'
    try:
        proc=subprocess.run(cmd,input=code.encode(),capture_output=True,timeout=remote_seconds+50)
        stem.with_suffix('.stdout').write_bytes(proc.stdout)
        stem.with_suffix('.stderr').write_bytes(proc.stderr)
        report=json.loads(proc.stdout) if proc.stdout.strip() else {'stage':'ssh'}
        report.update(ssh_exit=proc.returncode,ssh_stderr_bytes=len(proc.stderr))
        print(json.dumps(report,separators=(',',':')))
        return proc.returncode
    except Exception as error:
        stem.with_suffix('.error').write_text(traceback.format_exc(),encoding='utf-8')
        print(json.dumps(dict(stage=action,error_class=type(error).__name__,evidence='green_local_logs')))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
