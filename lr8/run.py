"""Package pinned TileXR and SST sources, then run the requested cases over SSH."""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

REPO = 'D:/workspace/TileXR'
TOOL = '96581a23de5e7e87ce90c770fa130a596600944e'
GENERATOR = '23a542b2e6bfcc34f85313d02677a4aafdfc3efb'
HERE = Path(__file__).resolve().parent

def git(*args):
    return subprocess.check_output(['git', '-C', REPO, *args], stderr=subprocess.PIPE, timeout=120)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    revision = args['revision']
    assert re.fullmatch('[0-9a-f]{40}', revision)
    assert re.fullmatch('[a-z0-9-]{1,45}', args['label'])
    assert args['cases'] and len(args['cases']) <= 16
    for ranks, elements in args['cases']:
        assert ranks in (8, 16, 32, 64)
        assert elements in (128, 512 * 1024 // (ranks * 4), 4 * 1024**2 // (ranks * 4), 16 * 1024**2 // (ranks * 4))
    for commit in (revision, TOOL, GENERATOR):
        git('fetch', '--no-tags', 'origin', commit)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        paths = ('tools/simulator', 'tools/tssh')
        archive = git('archive', TOOL, *paths)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(root, filter='data')
        sys.path.insert(0, str(root / 'tools/simulator'))
        from sources import package
        payload = root / 'sources.tar.gz'
        lock = package(REPO, revision, GENERATOR, payload)
        tool_dir = root / 'tools/simulator'
        sources = (payload.read_bytes(), (tool_dir / 'kernel_worker.py').read_bytes(),
                   (tool_dir / 'suite.py').read_bytes())
        data = [base64.b64encode(source).decode('ascii') for source in sources]
    program = 'ARGS = ' + repr(args) + '\nLOCK = ' + repr(lock) + '\nDATA = ' + repr(data) + '\n'
    program += (HERE / 'remote.py').read_text(encoding='utf-8')
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes',
         '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
         '-i', 'C:/Users/l00929943/.ssh/id_ed25519_server', 'root@141.61.94.30', 'python3 -'],
        input=program.encode('utf-8'), capture_output=True, timeout=args.get('batch_timeout', 1500))
    lines = result.stdout.decode('utf-8', 'replace').splitlines()
    summaries = [json.loads(line) for line in lines if line.startswith('{')]
    print(json.dumps(dict(ssh_exit=result.returncode, records=summaries,
                          stderr_bytes=len(result.stderr))))
    return result.returncode

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(stage='transport_or_staging', error_class=type(error).__name__)))
        sys.exit(1)
