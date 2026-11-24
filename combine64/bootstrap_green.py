# Copyright (c) 2026 TileXR Project.
"""Fetch the fixed public candidate and invoke its isolated green build."""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

SESSION='fac4ae28-1586-42fe-b55b-ff09982f17ec'
REVISION='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert args=={'action':'build'}
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr'/SESSION
    logs.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='combine-ht-source-') as temporary:
        def git(*argv):
            p=subprocess.run(['git','-C',temporary,*argv],capture_output=True,timeout=180)
            if p.returncode:
                (logs/'source-git.stderr').write_bytes(p.stderr)
                raise RuntimeError('source_git_failed_see_green_log')
            return p.stdout
        git('init','-q')
        git('fetch','--depth','1','https://github.com/LingquLab/TileXR.git',REVISION)
        assert git('rev-parse','FETCH_HEAD').decode().strip()==REVISION
        tree=git('rev-parse','FETCH_HEAD^{tree}').decode().strip()
        archive=git('archive','--format=tar.gz',REVISION)
    directory=Path(__file__).parent
    request={'revision':REVISION,'tree':tree,'source_sha256':hashlib.sha256(archive).hexdigest(),
             'source':base64.b64encode(archive).decode(),'build_script':(directory/'build.sh').read_text(encoding='utf-8')}
    source=(directory/'bootstrap_host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(request)+'))\n'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    argv=['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
          '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
          'timeout --kill-after=20 1380 python3 -']
    p=subprocess.run(argv,input=source.encode(),capture_output=True,timeout=1420)
    (logs/'bootstrap.stdout').write_bytes(p.stdout)
    (logs/'bootstrap.stderr').write_bytes(p.stderr)
    if p.stdout.strip():
        result=json.loads(p.stdout)
        assert len(p.stdout)<30000
        result['ssh_stderr_bytes']=len(p.stderr)
        print(json.dumps(result,separators=(',',':')))
    else:
        print(json.dumps({'stage':'bootstrap','ssh_exit':p.returncode,'evidence':'green_local_logs'}))
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
