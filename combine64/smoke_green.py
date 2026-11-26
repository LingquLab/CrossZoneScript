# Copyright (c) 2026 TileXR Project.
"""Build one fixed candidate and run its first bs32 send case in sequence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',required=True,type=Path)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert args=={'candidate':'b61cb53','attempt':3}
    here=Path(__file__).parent
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/59af0a93-b62f-4211-927e-a44f047667cf/b61cb53-smoke-a3'
    logs.mkdir(parents=True,exist_ok=False)
    phases=[('build','bootstrap_green.py',dict(action='build',candidate='b61cb53'),1510),
            ('send32','sim_green.py',dict(action='inter-server-send',tokens=32,attempt=3,timeout=2400,candidate='b61cb53'),3100)]
    reports=[]
    passed=True
    for label,entry,parameters,timeout in phases:
        request=logs/(label+'-args.json')
        request.write_text(json.dumps(parameters),encoding='utf-8')
        with (logs/(label+'.stdout')).open('wb') as out,(logs/(label+'.stderr')).open('wb') as err:
            p=subprocess.run([sys.executable,str(here/entry),'--args-file',str(request)],stdout=out,stderr=err,timeout=timeout)
        raw=(logs/(label+'.stdout')).read_bytes()
        saved=json.loads(raw) if raw.strip() else {}
        if label=='build' and p.returncode==0:
            assert saved['build']['all_compiled'] and saved['profile_build']['all_compiled'] and saved['inter_server_rank_send']
            summary={key:saved[key] for key in ('revision','container_id','root','wall_seconds')}
            summary['objects']={key:value['sha256'] for key,value in saved['profile_build']['targets'].items() if key.endswith(('inter_server_send_kernel','pipeline_sst_kernel'))}
        else:
            summary={key:value for key,value in saved.items() if key not in ('checked_bytes',)}
        reports.append(dict(phase=label,exit_code=p.returncode,summary=summary))
        (logs/'result.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
        if p.returncode:
            passed=False
            break
    raw=json.dumps(dict(phases=reports),separators=(',',':'))
    assert len(raw.encode())<=4096
    print(raw)
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
