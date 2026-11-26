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

SESSION='44b9c060-a5dd-46a8-897f-e4a6fee171e2'
REVISIONS = {
    '518b9df': '518b9dfa02dc3d33852ebecea90143d569ea50af',
    'd04fde0': 'd04fde0c3d7b3e9017ca8d43383b9183fd7a2354',
    '8e005fa': '8e005faf781c14dab3c0e977b52741ee29b597ac',
    '85d4b8d': '85d4b8de182b0c813d586f7278920489c0c5c4a6',
}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert args.get('action') == 'build' and set(args) <= {'action', 'candidate'}
    candidate = args.get('candidate', '518b9df')
    revision = REVISIONS[candidate]
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr'/SESSION/candidate
    logs.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='combine-ht-source-') as temporary:
        def git(*argv):
            p=subprocess.run(['git','-C',temporary,*argv],capture_output=True,timeout=180)
            if p.returncode:
                (logs/'source-git.stderr').write_bytes(p.stderr)
                raise RuntimeError('source_git_failed_see_green_log')
            return p.stdout
        git('init','-q')
        git('fetch','--depth','1','https://github.com/LingquLab/TileXR.git',revision)
        assert git('rev-parse','FETCH_HEAD').decode().strip()==revision
        tree=git('rev-parse','FETCH_HEAD^{tree}').decode().strip()
        archive=git('archive','--format=tar.gz',revision)
    directory=Path(__file__).parent
    request={'candidate':candidate,'revision':revision,'tree':tree,'source_sha256':hashlib.sha256(archive).hexdigest(),
             'source':base64.b64encode(archive).decode(),'build_script':(directory/'build.sh').read_text(encoding='utf-8'),
             'profile_files':{name:(directory/name).read_text(encoding='utf-8')
                              for name in ('build_profile.py', 'profile_capture.h')}}
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
    try:
        result = main()
    except Exception as error:
        print(json.dumps({'stage': 'bootstrap', 'error_class': type(error).__name__, 'evidence': 'green_local_logs'}))
        result = 1
    raise SystemExit(result)
