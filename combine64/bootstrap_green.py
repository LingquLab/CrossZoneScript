# Copyright (c) 2026 TileXR Project.
"""Fetch the pinned candidate and invoke its isolated green build."""
import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from ssh_target import run
REVISIONS = {'49f846b': '49f846bdba882ecc0b708b0ec18f56d474b252c0'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    candidate = args.get('candidate', '49f846b')
    revision = REVISIONS[candidate]
    with tempfile.TemporaryDirectory(prefix='combine-ht-source-') as temporary:
        def git(*argv):
            p=subprocess.run(['git','-C',temporary,*argv],capture_output=True,timeout=180)
            if p.returncode:
                raise RuntimeError('source_git_failed')
            return p.stdout
        git('init','-q')
        git('fetch','--depth','1','https://github.com/LingquLab/TileXR.git',revision)
        assert git('rev-parse','FETCH_HEAD').decode().strip()==revision
        tree=git('rev-parse','FETCH_HEAD^{tree}').decode().strip()
        archive=git('archive','--format=tar.gz',revision)
    directory=Path(__file__).parent
    request={**args,'candidate':candidate,'revision':revision,'tree':tree,'source_sha256':hashlib.sha256(archive).hexdigest(),
             'source':base64.b64encode(archive).decode(),'build_script':(directory/'build.sh').read_text(encoding='utf-8'),
             'profile_files':{name:(directory/name).read_text(encoding='utf-8')
                              for name in ('build_profile.py', 'profile_capture.h')}}
    code = (directory / 'bootstrap_host.py').read_text(encoding='utf-8')
    return 0 if run(code, request, 'bootstrap', 1810) else 1


if __name__=='__main__':
    try:
        result = main()
    except Exception as error:
        print(json.dumps({'stage': 'bootstrap', 'error_class': type(error).__name__, 'evidence': 'green_local_logs'}))
        result = 1
    raise SystemExit(result)
