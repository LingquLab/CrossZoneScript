# Copyright (c) 2026 TileXR Project.
"""Retain the candidate build report locally and return only bounded build evidence."""
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
    assert args=={'candidate':'e7da7b1'}
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/59af0a93-b62f-4211-927e-a44f047667cf/e7da7b1-build'
    logs.mkdir(parents=True,exist_ok=False)
    request=logs/'args.json'
    request.write_text(json.dumps(dict(action='build',candidate=args['candidate'])),encoding='utf-8')
    with (logs/'stdout').open('wb') as out,(logs/'stderr').open('wb') as err:
        p=subprocess.run([sys.executable,str(Path(__file__).with_name('bootstrap_green.py')),'--args-file',str(request)],
                         stdout=out,stderr=err,timeout=1510)
    raw=(logs/'stdout').read_bytes()
    result=json.loads(raw) if raw.strip() else {}
    summary={key:result[key] for key in ('revision','root','container_id','image','wall_seconds','build_exit','inter_server_rank_send') if key in result}
    if p.returncode==0:
        assert result['revision']=='e7da7b11d072fad95a8c58804839a1c867f205bd'
        assert result['build']['all_compiled'] and result['profile_build']['all_compiled'] and result['inter_server_rank_send']
        summary.update(normal_compiled=True,profiling_compiled=True)
        summary['objects']={key:value['sha256'] for key,value in result['profile_build']['targets'].items()
                            if key.endswith(('inter_server_send_kernel','final_reduce_kernel','pipeline_sst_kernel'))}
    packet=json.dumps(dict(phase='candidate_build',exit_code=p.returncode,summary=summary),separators=(',',':'))
    assert len(packet.encode())<=4096
    print(packet)
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
