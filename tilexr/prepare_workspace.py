"""Prepare isolated fixed-revision checkouts and discover simulator assets."""
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr'


def git(*args, timeout=180):
    p = subprocess.run(['git', *map(str, args)], capture_output=True, timeout=timeout)
    if p.returncode:
        (ROOT / 'git-preparation.stderr').write_bytes(p.stderr)
        raise RuntimeError('Git preparation failed; diagnostic saved green-local')
    return p.stdout.decode('utf-8', errors='strict').strip()


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    report = {'repository_prepared': False}
    remote = subprocess.run([
        'ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519', '-p', '22',
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=12', 'root@141.61.94.26', 'timeout 100 python3 -',
    ], input=Path(__file__).with_name('discover_assets.py').read_bytes(), capture_output=True, timeout=115)
    (ROOT / 'asset-discovery.stderr').write_bytes(remote.stderr)
    if remote.returncode:
        raise RuntimeError('Asset discovery failed; diagnostic saved green-local')
    report['asset_discovery'] = json.loads(remote.stdout)
    (ROOT / 'asset-discovery.json').write_text(json.dumps(report['asset_discovery'], indent=2), encoding='utf-8')
    repo = ROOT / 'TileXR-reproduction'
    if not repo.exists():
        git('clone', '--no-checkout', '--filter=blob:none', '--single-branch', '--branch',
            'codex/alltoall-sst-matrix-20261119', 'https://github.com/LingquLab/TileXR.git', repo)
    if git('-C', repo, 'remote', 'get-url', 'origin') != 'https://github.com/LingquLab/TileXR.git':
        raise RuntimeError('Unexpected origin in dedicated reproduction repository')
    git('-C', repo, 'fetch', 'origin', 'codex/sst-local7-shapes-20261111', 'codex/unitest-sst-input-abi')
    versions = {'runtime': '85afd1fa61669a5ef800a80bec4df93745f119c6',
                'tool': '96581a23de5e7e87ce90c770fa130a596600944e',
                'unitest': '22c95063d09d63d2d87a10525526a4c603f208bd',
                'generator': '23a542b2e6bfcc34f85313d02677a4aafdfc3efb'}
    paths = {}
    for name, revision in versions.items():
        actual = git('-C', repo, 'rev-parse', revision + '^{commit}')
        if actual != revision:
            raise RuntimeError('Requested Git revision not available')
        if name == 'generator':
            continue
        path = ROOT / ('TileXR-' + name)
        if not path.exists():
            git('-C', repo, 'worktree', 'add', '--detach', path, revision)
        if git('-C', path, 'rev-parse', 'HEAD') != revision or git('-C', path, 'status', '--porcelain'):
            raise RuntimeError('Reproduction checkout identity/cleanliness mismatch')
        paths[name] = str(path)
    inventory = ROOT / 'inventory.json'
    inventory.write_text(json.dumps({'version': 1, 'clusters': {'tilexr-repro': {
        'user': 'root', 'hosts': [{'name': 'host-94-26', 'address': '141.61.94.26'}]}}}, indent=2), encoding='utf-8')
    report.update(repository_prepared=True, checkouts=paths, versions=versions, inventory=str(inventory))
    (ROOT / 'workspace.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
