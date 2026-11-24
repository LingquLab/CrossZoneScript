# Copyright (c) 2026 TileXR Project.
"""Map the retained LSU address and inspect a bounded device instruction excerpt."""

import json
from pathlib import Path
import re
import struct
import subprocess
import sys

sys.path.insert(0, '/work')
import sst

FOLDER = Path('/work/resume-7de1/mte-a1')
FAULT = 563912026117120
workload = json.loads((FOLDER / 'send-only/workload.json').read_text())
snapshot = json.loads(Path('/work/environment.snapshot.json').read_text())
matches, sample = [], []
for rank in range(8):
    config = workload['workload_config']['chip' + str(rank)]
    source = Path(config['kernel_param']['load_sq_ctx']['input_bin']).read_bytes()
    for peer in range(8):
        for qp in range(8):
            offset = (peer * 40 + qp) * 80
            doorbell = struct.unpack_from('<Q', source, offset + 48)[0]
            row = dict(rank=rank, peer=peer, qp=qp, doorbell_hex=hex(doorbell))
            if rank == 6 and peer == 2 and qp == 2:
                sample.append(row)
            if FAULT == doorbell or FAULT == doorbell * 8:
                matches.append(dict(**row, relation='exact' if FAULT == doorbell else 'times_eight'))
compiler = Path(snapshot['settings']['compiler'])
cann = Path(snapshot['settings']['cann_root'])
tools = [compiler.parent / name for name in ('llvm-objdump', 'cce-objdump')]
tools += list(cann.glob('**/bin/llvm-objdump'))[:4]
tool = next((path for path in tools if path.is_file()), None)
disassembly = dict(available=bool(tool))
if tool:
    proc = subprocess.run([str(tool), '-d', str(FOLDER / 'tilexr_moonep_combine_ht_kernel.o')], capture_output=True, text=True, timeout=35)
    lines = proc.stdout.splitlines()
    hits = [i for i, line in enumerate(lines) if re.search(r'\bst_dev\b', line, re.I)]
    indices = sorted({j for i in hits[:2] for j in range(max(0, i - 2), min(len(lines), i + 2))})[:8]
    disassembly.update(tool=str(tool), exit_code=proc.returncode, st_dev_count=len(hits),
                        excerpts=[lines[i][:350] for i in indices], output_bytes=len(proc.stdout),
                        diagnostic=proc.stderr[:350] if proc.returncode else '')
log = FOLDER / 'send-only/sim.log'
hits = []
pattern = re.compile(r'Unknown low level|st_dev|ST_DEV|doorbell|Doorbell')
with log.open(errors='replace') as handle:
    for line in handle:
        if pattern.search(line):
            hits.append(line.strip()[:550])
            if len(hits) > 6:
                hits.pop(0)
print(json.dumps(dict(ok=True, read_only=True, fault_hex=hex(FAULT), gm_bytes=hex(sst.rt.GM_BYTES),
    doorbell_matches=matches[:16], sample=sample, disassembly=disassembly, relevant_log_tail=hits,
    compile_flags=[item for item in json.loads((FOLDER / 'build.json').read_text())[0]
                   if item.startswith('-') and not item.startswith('-I')]), ensure_ascii=True))
