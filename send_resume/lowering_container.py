# Copyright (c) 2026 TileXR Project.
"""Generate isolated compiler listings for the retained failing MTE candidate."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, '/work')
import sst

folder = Path('/work/resume-7de1/lowering-a1')
folder.mkdir(exist_ok=False)
base = Path('/work/resume-7de1/mte-a1')
assert sst.checksum(base / 'tilexr_moonep_combine_ht_kernel.o') == 'efe602e3973951d3059c3af8ff371166140a2dfb2687333d71b1deedb7288a36'
original = json.loads((base / 'build.json').read_text())[0]
snapshot = json.loads(Path('/work/environment.snapshot.json').read_text())
results = []
for name, llvm, auto in [('auto', False, True), ('manual', False, False), ('ir', True, True)]:
    output = folder / (name + ('.ll' if llvm else '.s'))
    args = original[:]
    args[args.index('-c')] = '-S'
    args[args.index('-o') + 1] = str(output)
    if llvm:
        args.insert(1, '-emit-llvm')
    if not auto:
        args.remove('--cce-auto-sync')
    with (folder / (name + '.log')).open('wb') as log:
        proc = subprocess.run(sst.shell_command(snapshot['settings'], 'cann', args), stdout=log, stderr=subprocess.STDOUT, timeout=150)
    row = dict(name=name, exit_code=proc.returncode, output=str(output))
    if proc.returncode == 0 and output.is_file():
        text = output.read_text(errors='replace')
        lines = text.splitlines()
        hits = [i for i, line in enumerate(lines) if re.search(r'st[_\.]?dev|stg|store.*volatile|llvm.*st.*dev', line, re.I)]
        row.update(output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(), output_bytes=output.stat().st_size,
                   matching_lines=len(hits), snippets=[lines[max(0,i-3):i+3] for i in hits[:2]])
        operations = Counter()
        if not llvm:
            for line in lines:
                if re.match(r'^\s+[A-Za-z][A-Za-z0-9_\.]*\s+', line):
                    operations[line.split()[0]] += 1
            row['operations'] = operations.most_common(40)
            row['first_instructions'] = [line[:300] for line in lines if re.match(r'^\s+[A-Za-z][A-Za-z0-9_\.]*\s+', line)][:4]
        else:
            row['memory_intrinsics'] = sorted({line.strip().split('(')[0][-180:] for line in lines
                if 'declare' in line and any(s in line.lower() for s in ['st.', 'store', 'dev'])})[:15]
    else:
        log = (folder / (name + '.log')).read_text(errors='replace')
        row['diagnostics'] = [line[:350] for line in log.splitlines() if 'error:' in line][:3]
    results.append(row)
result = dict(ok=all(row['exit_code'] == 0 for row in results), diagnostic_only=True, simulator_run=False, report=str(folder), builds=results)
(folder / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=True))
raise SystemExit(0 if result['ok'] else 1)
