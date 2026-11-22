"""中文：从既有 origin 取固定 Combine 提交，在独立目录构建 TRACE，不启动 NPU。
English: Fetch the pinned Combine revision from existing origin; build isolated TRACE artifacts without NPU launches.
"""
import base64
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import sys
from common import HOSTS, ROOT, REVISION, REMOTE_ROOT, remote_python, json_rows


def run(label, argv, timeout=180):
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    (ROOT / ('ep32-' + label + '.log')).write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, label + ' failed; retained local log'
    return result.stdout


def main():
    repo = Path(r'D:\project\tileXR')
    assert run('origin', ['git', '-C', str(repo), 'remote', 'get-url', 'origin']).decode().strip() == 'https://github.com/LingquLab/TileXR.git'
    run('fetch', ['git', '-C', str(repo), 'fetch', 'origin', 'refs/heads/codex/ubx-combine-push'])
    assert run('revision', ['git', '-C', str(repo), 'rev-parse', REVISION + '^{commit}']).decode().strip() == REVISION
    source = ROOT / ('source-' + REVISION[:8])
    assert not source.exists(), 'Source snapshot exists; inspect previous preparation first'
    run('worktree', ['git', '-c', 'core.hooksPath=NUL', '-C', str(repo), 'worktree', 'add', '--detach', str(source), REVISION])
    run('dependencies', ['git', '-c', 'core.hooksPath=NUL', '-C', str(source), 'submodule', 'update', '--init', '--depth', '1', '--', '3rdparty/spdlog', '3rdparty/picojson'], 300)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for path in sorted(source.rglob('*')):
            rel = path.relative_to(source)
            if '.git' in rel.parts or '__pycache__' in rel.parts:
                continue
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=str(rel).replace('\\', '/'), recursive=False)
    raw = data.getvalue()
    (ROOT / 'ep32-source.tar.gz').write_bytes(raw)
    spec = dict(root=REMOTE_ROOT, revision=REVISION, source_sha256=hashlib.sha256(raw).hexdigest(), source_b64=base64.b64encode(raw).decode())
    template = Path(__file__).with_name('remote_build.py').read_text(encoding='utf-8')
    script = 'SPEC=' + repr(spec) + '\n' + template
    def one(host):
        result = remote_python(host, script, timeout=2100, label='ep32-build')
        return dict(host=host, exit=result.returncode, rows=json_rows(result.stdout), stderr_bytes=len(result.stderr))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(one, HOSTS))
    answer = dict(revision=REVISION, remote_root=REMOTE_ROOT, source_sha256=spec['source_sha256'], hosts=rows, hardware_started=False)
    (ROOT / 'ep32-build-result.json').write_text(json.dumps(answer, indent=2), encoding='utf-8')
    print(json.dumps(answer, separators=(',', ':')))
    return 0 if all(row['exit'] == 0 and len(row['rows']) == 1 and row['rows'][0].get('status') == 'BUILD_READY' for row in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
