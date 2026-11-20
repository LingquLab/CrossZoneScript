"""Find only relevant simulator metadata under the known maintainer directory."""
import json
import os
from pathlib import Path
import time


root = Path('/mnt/share/h30059441')
result = {'root': str(root), 'directories': [], 'prefill_candidates': [], 'scenarios_inspected': 0}
deadline = time.monotonic() + 65
prune = {'.git', 'node_modules', '__pycache__', 'input_bin', 'golden', 'bin', 'OUTPUT', 'dumps',
         'Ascend', 'ascend-toolkit', 'site-packages', 'local', 'lib', 'include', 'logs', 'stat_trace'}
if root.is_dir():
    result['directories'] = sorted(p.name for p in root.iterdir() if p.is_dir() and any(
        term in p.name.lower() for term in ['tilexr', 'alltoall', 'sst', 'matrix', 'prefill', 'profile', 'unitest', 'sim']))[:80]
    bases = [root / name for name in result['directories'] if not name.lower().startswith('group_alltoall_8p_main_20261029')]
    for base in bases:
        for current, dirs, files in os.walk(base):
            depth = len(Path(current).relative_to(base).parts)
            dirs[:] = [name for name in dirs if name not in prune and depth < 6]
            if time.monotonic() > deadline:
                result['search_time_limited'] = True
                break
            path = Path(current)
            for name in ['scenario.json', 'build_manifest.json']:
                if name not in files:
                    continue
                metadata = path / name
                if metadata.stat().st_size > 2 * 1024 * 1024:
                    continue
                try:
                    value = json.loads(metadata.read_text())
                except (ValueError, OSError):
                    continue
                result['scenarios_inspected'] += name == 'scenario.json'
                identity = {key: value[key] for key in ['rank_size', 'elements_per_peer', 'kernel_entry',
                    'kernel_name', 'entry', 'binary_sha256', 'source_commit', 'runtime_commit', 'stage_profiling']
                    if key in value and isinstance(value[key], (str, int, bool))}
                if 'prefill' in str(path).lower() or 'prefill' in json.dumps(identity).lower():
                    result['prefill_candidates'].append({'path': str(metadata), 'identity': identity})
                    if len(result['prefill_candidates']) >= 30:
                        break
            if len(result['prefill_candidates']) >= 30:
                break
        if time.monotonic() > deadline or len(result['prefill_candidates']) >= 30:
            break
print(json.dumps(result, ensure_ascii=True))
