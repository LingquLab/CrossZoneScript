# Copyright (c) 2026 TileXR Project.
"""Read short instruction and register excerpts from the identified faulting core."""

from collections import deque
import json
from pathlib import Path
import re

folder = Path('/work/resume-7de1/trace-a1/send-only/dumps')
result = dict(ok=True, read_only=True, diagnostic_only=True, send_pass=False, files=[])
for suffix in ('su.dump', 'sudetail.dump'):
    path = folder / ('SU_David_V100_0_aicore1_aiv0_' + suffix)
    assert path.is_file(), 'fault_core_dump_missing'
    head, tail, hits = [], deque(maxlen=8), deque(maxlen=4)
    pattern = re.compile(r'st[_\.]?dev|ld[_\.]?dev|dcci', re.I)
    with path.open(errors='replace') as handle:
        for number, line in enumerate(handle, 1):
            text = line.strip()[:900]
            if len(head) < 2:
                head.append(text)
            tail.append(dict(line=number, text=text))
            match = pattern.search(line)
            if match:
                hits.append(dict(line=number, text=line[max(0,match.start()-100):match.start()+750].strip()))
    result['files'].append(dict(path=str(path), bytes=path.stat().st_size,
                               head=head, tail=list(tail), device_ops=list(hits)))
print(json.dumps(result, ensure_ascii=True))
