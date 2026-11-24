# Copyright (c) 2026 TileXR Project.
"""Read bounded failure context from the completed MTE Send run."""

from collections import deque
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess

WORK = Path('/var/tmp/tilexr-combine-ht-500e2a93-b84e-4d36-a9aa-8d2cd0321cdf-v11')
NAME = 'tilexr-combine-ht-500e2a93-v11'
assert socket.gethostname().split('.')[0] == 'host-94-26'
proc = subprocess.run(['docker', 'inspect', '--format', '{{.Id}}', NAME], capture_output=True, text=True, timeout=25)
assert proc.returncode == 0 and proc.stdout.strip() == 'e9ab0e97f6b31670b6ce68412fdf20c15203502b9aa8fc0840d45e927a0254a4'
proc = subprocess.run(['docker', 'top', NAME, '-eo', 'pid,comm,stat'], capture_output=True, text=True, timeout=25)
assert proc.returncode == 0
rows = [line.split() for line in proc.stdout.splitlines()[1:]]
assert not any(row[1] in ('sst', 'sstsim.x') and not row[2].startswith('Z') for row in rows), 'sst_active'
folder = WORK / 'resume-7de1/mte-a1'
assert hashlib.sha256((folder / 'tilexr_moonep_combine_ht_kernel.o').read_bytes()).hexdigest() == 'efe602e3973951d3059c3af8ff371166140a2dfb2687333d71b1deedb7288a36'
case = folder / 'send-only'
log = case / 'sim.log'
counts, first, tail, previous = {}, [], deque(maxlen=10), deque(maxlen=2)
pattern = re.compile(r'FATAL|ERROR|[Aa]ssertion|SIGSEGV|Segmentation fault|[Ee]xception|[Ff]ailed|[Ii]nvalid|abort|[Uu]nsupported')
with log.open(errors='replace') as handle:
    for number, line in enumerate(handle, 1):
        text = line.strip()[:650]
        tail.append(dict(line=number, text=text))
        matches = pattern.findall(line)
        for match in matches:
            counts[match] = counts.get(match, 0) + 1
        if matches and len(first) < 6:
            first.append(dict(line=number, text=text, before=list(previous)))
        previous.append(text)
stderr = WORK / 'resume-7de1/mte-a1.stderr'
print(json.dumps(dict(ok=True, read_only=True, no_sst_active=True,
    kernel_sha256='efe602e3973951d3059c3af8ff371166140a2dfb2687333d71b1deedb7288a36',
    log_bytes=log.stat().st_size, error_counts=counts, first_errors=first, tail=list(tail),
    wrapper_stderr=stderr.read_text(errors='replace')[:1500] if stderr.exists() else '',
    dump_files=len(list((case / 'bin').glob('*.bin')))), ensure_ascii=True))
