# Copyright (c) 2026 TileXR Project.
"""Fetch the pinned candidate and invoke its isolated green build."""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

SESSION='1a5bdfed-7c8c-47ca-b7b6-827c1777651a'
REVISIONS = {'c030900': 'c03090090b9abf3ccf3b5ce51fb3aaf3d9d3cd32'}


def source_request(candidate, revision, logs):
    with tempfile.TemporaryDirectory(prefix='combine-ht-source-') as temporary:
        def git(*argv):
            command = ['git', '-C', temporary, *argv]
            proc = subprocess.run(command, capture_output=True, timeout=180)
            if proc.returncode:
                (logs/'source-git.stderr').write_bytes(proc.stderr)
                raise RuntimeError('source_git_failed_see_green_log')
            return proc.stdout
        git('init', '-q')
        git('fetch', '--depth', '1', 'https://github.com/LingquLab/TileXR.git', revision)
        assert git('rev-parse', 'FETCH_HEAD').decode().strip() == revision
        tree = git('rev-parse', 'FETCH_HEAD^{tree}').decode().strip()
        archive = git('archive', '--format=tar.gz', revision)
    directory = Path(__file__).parent
    return {
        'candidate': candidate,
        'revision': revision,
        'tree': tree,
        'source_sha256': hashlib.sha256(archive).hexdigest(),
        'source': base64.b64encode(archive).decode(),
        'build_script': (directory/'build.sh').read_text(encoding='utf-8'),
        'profile_files': {name: (directory/name).read_text(encoding='utf-8')
                          for name in ('build_profile.py', 'profile_capture.h')},
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    args=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert args.get('action') in ('build', 'build-status') and set(args) <= {'action', 'candidate'}
    candidate = args.get('candidate', 'c030900')
    revision = REVISIONS[candidate]
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr'/SESSION/candidate
    if args['action'] == 'build-status':
        report = {'revision': revision}
        for name in ('source-git.stderr', 'bootstrap.stderr', 'bootstrap.stdout'):
            path = logs / name
            report[name] = path.stat().st_size if path.is_file() else None
        if report['bootstrap.stdout']:
            saved = json.loads((logs / 'bootstrap.stdout').read_text(encoding='utf-8'))
            assert saved['revision'] == revision
            for stage in ('build', 'profile'):
                if stage in saved:
                    report[stage] = {name: {key: row[key] for key in ('exit_code', 'errors') if key in row}
                                     for name, row in saved[stage].get('targets', {}).items()}
            report.update({key: saved[key] for key in ('build_exit', 'profile_exit') if key in saved})
        print(json.dumps(report, separators=(',', ':')))
        return 0
    logs.mkdir(parents=True,exist_ok=True)
    request = source_request(candidate, revision, logs)
    directory=Path(__file__).parent
    source=(directory/'bootstrap_host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(request)+'))\n'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    argv=['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
          '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
          'timeout --kill-after=20 1800 python3 -']
    p=subprocess.run(argv,input=source.encode(),capture_output=True,timeout=1845)
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
