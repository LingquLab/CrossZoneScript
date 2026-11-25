"""Read only the preserved logs from this task's minimal device probe."""
import json
from pathlib import Path
import re

root = Path('/home/glm16-09b20820')
out = []
for name in ['device-probe-create.stderr.log', 'device-probe.stdout.log', 'device-probe.stderr.log']:
    p = root / name
    if not p.is_file():
        continue
    text = p.read_text(errors='replace')
    lines = [x for x in text.splitlines() if re.search(r'(?i)error|fail|507033|ERR00100|denied|permission', x)]
    out.append({'file': str(p), 'bytes': p.stat().st_size,
                'error_lines': [x[:600] for x in lines[:8]]})
print(json.dumps({'probe_logs': out}))
