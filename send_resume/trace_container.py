# Copyright (c) 2026 TileXR Project.
"""Capture model instructions for the known synthetic Send failure on rank zero."""

import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, '/work')
import sst

binary = Path('/work/resume-7de1/manual-a1/tilexr_moonep_combine_ht_kernel.o')
assert sst.checksum(binary) == '184ff37bee2923dcfa2d8a596df25ffdad94aeaeff6c4c71d28d3ab18e139b28'
folder = Path('/work/resume-7de1/trace-a1')
folder.mkdir(exist_ok=False)
snapshot = json.loads(Path('/work/environment.snapshot.json').read_text())
sst.verify_environment(snapshot)
original = sst.configure


def configure(sources, args):
    args.instruction_dump_chips = [0]
    return original(sources, args)


sst.configure = configure
sst.KERNEL, sst.WORK = binary, folder
specs, routes = sst.fixtures.prepare(8, 4, 256, 16, 4, 40, 64)
route_hash = hashlib.sha256(json.dumps(routes, separators=(',', ':')).encode()).hexdigest()
assert route_hash == 'f5eb786c6ca0a725607b16caef0ac8a8c0efc161576b185d9c74ffa2f4127881'
send = sst.run_phase(folder / 'send-only', 1, specs, [spec.serialize() for spec in specs], snapshot,
                     dict(mode='send', threads=8, stage_timeout=240))
files = []
for path in sorted((folder / 'send-only').rglob('*')):
    if path.is_file() and path.suffix.lower() not in ('.bin', '.json', '.yml', '.py'):
        name = str(path.relative_to(folder))
        if any(key in name.lower() for key in ('dump', 'instr', 'trace')) and path.name not in ('sim.log', 'model.log'):
            files.append(dict(path=name, bytes=path.stat().st_size))
fatal = []
with (folder / 'send-only/sim.log').open(errors='replace') as handle:
    for line in handle:
        match = re.search(r'FATAL|Unknown low level|[Aa]ssertion', line)
        if match and len(fatal) < 2:
            fatal.append(line[max(0,match.start()-30):match.start()+800].strip())
snippets = []
for record in files:
    name = record['path'].lower()
    if ('core1' in name or 'core_1' in name) and ('instr' in name or 'scalar' in name):
        with (folder / record['path']).open('rb') as handle:
            handle.seek(max(0, record['bytes'] - 65536))
            lines = handle.read().decode(errors='replace').splitlines()
        snippets.append(dict(path=record['path'], tail=[line[:450] for line in lines[-6:]]))
        if len(snippets) >= 2:
            break
result = dict(ok=bool(files) and bool(fatal) and not send['timed_out'], diagnostic_only=True,
    send_pass=False, report=str(folder), kernel_sha256=sst.checksum(binary), route_sha256=route_hash,
    send={k: send[k] for k in ('returncode', 'timed_out', 'completed', 'failures', 'sst_wall_s', 'simulated_us') if k in send},
    model_dump_chips=[0], fatal=fatal, instruction_file_count=len(files), instruction_bytes=sum(row['bytes'] for row in files),
    files=files[:24], instruction_snippets=snippets)
(folder / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, ensure_ascii=True))
raise SystemExit(0 if result['ok'] else 1)
