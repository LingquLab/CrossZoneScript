"""Read only saved general107 build evidence; no build or device query."""
from pathlib import Path
import hashlib
import json
import os
import re
import sys

assert len(sys.argv) in (2, 3) and sys.argv[1] in ('01', '02')
assert len(sys.argv) == 2 or sys.argv[2] == 'headers'
ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run' + sys.argv[1])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def log_summary(path):
    if not path.is_file():
        return {'exists': False}
    raw = path.read_bytes()
    lines = raw.decode('utf-8', errors='replace').splitlines()
    errors = [line[:350] for line in lines if re.search(r'error:|fatal error:|Error |Error:|undefined reference|Could NOT|CMake Error|FAILED:', line)]
    # No broad log tails, environment or command lines. Error text only.
    errors = [line for line in errors if not re.search(r'(?i)authorization|password|token=|secret|api.key|https?://', line)]
    return {'exists': True, 'bytes': len(raw), 'sha256': sha(raw), 'errors': errors[:4]}


def main():
    assert os.geteuid() == 0
    assert sha(Path('/etc/machine-id').read_bytes()) == '78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
    result = {'read_only': True, 'npu_launch': False, 'device_queries': False, 'directory_exists': ROOT.is_dir()}
    if ROOT.is_dir():
        path = ROOT / 'build-summary.json'
        result['build_summary'] = {'exists': path.is_file()}
        if path.is_file():
            obj = json.loads(path.read_text())
            keys = ('schema', 'status', 'candidate', 'npu_launch', 'clock', 'error_type', 'error_detail', 'cores', 'runtime_validation', 'artifacts')
            result['build_summary'].update(sha256=sha(path.read_bytes()), fields={k: obj[k] for k in keys if k in obj})
        result['receipts'] = {}
        for path in sorted(ROOT.glob('*.receipt.json')):
            obj = json.loads(path.read_text())
            result['receipts'][path.name] = {k: obj[k] for k in ('exit_code', 'timed_out', 'seconds') if k in obj}
        failed = [name[:-len('.receipt.json')] for name, row in result['receipts'].items() if row.get('exit_code') != 0 or row.get('timed_out')]
        result['logs'] = {name + '.log': log_summary(ROOT / (name + '.log')) for name in failed[:4]}
        for name in ('build-parent.stdout.log', 'build-parent.stderr.log'):
            result['logs'][name] = log_summary(ROOT / name)
        result['live_matching_processes'] = []
        result['process_read_errors'] = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmd = (proc / 'cmdline').read_bytes()
                fields = (proc / 'stat').read_text().rsplit(') ', 1)[1].split()
                if fields[0] != 'Z' and str(ROOT).encode() in cmd:
                    result['live_matching_processes'].append({'pid': int(proc.name), 'start_ticks': fields[19], 'cmdline_sha256': sha(cmd)})
            except (FileNotFoundError, ProcessLookupError):
                pass
            except OSError:
                result['process_read_errors'].append(int(proc.name))
        result['phase_directories_exist'] = {name: (ROOT / name).exists() for name in ('correctness', 'capture', 'correctness-launch', 'capture-launch')}
        if len(sys.argv) == 3:
            toolkit = Path('/usr/local/Ascend/cann-9.1.0')
            prefixes = ('include', 'pkg_inc', 'include/experiment', 'include/experiment/runtime',
                        'aarch64-linux/include', 'aarch64-linux/pkg_inc',
                        'aarch64-linux/include/experiment', 'aarch64-linux/include/experiment/runtime')
            result['header_locations'] = {}
            for relative in ('runtime/kernel.h', 'acl/acl.h', 'hccl/hccl.h'):
                matches = []
                for prefix in prefixes:
                    header = toolkit / prefix / relative
                    if header.is_file():
                        matches.append({'include_root': str(header.parent.parent), 'path': str(header),
                                        'resolved_path': str(header.resolve()), 'sha256': sha(header.read_bytes())})
                result['header_locations'][relative] = matches
            path = ROOT / 'runner-configure.log'
            if path.is_file():
                raw = path.read_bytes()
                text = raw.decode('utf-8', errors='strict')
                assert len(raw) <= 2048 and len(text.splitlines()) <= 24
                assert not re.search(r'(?i)authorization|password|token=|secret|api.key|https?://', text)
                result['runner_configure_diagnostic'] = text
    text = json.dumps(result, ensure_ascii=True, separators=(',', ':'))
    assert len(text.encode()) < 12000
    print(text)


if __name__ == '__main__':
    main()
