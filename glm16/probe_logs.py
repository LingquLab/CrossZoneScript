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
latest = root / 'device-probe-latest.json'
if latest.is_file():
    info = json.loads(latest.read_text())
    folder = Path(info['log_directory'])
    assert folder.parent == root and re.fullmatch(r'device-probe-\d+', folder.name)
    for p in sorted(folder.rglob('*.log'))[:12]:
        with p.open('rb') as stream:
            text = stream.read(512 * 1024).decode('utf-8', 'replace')
        lines = [x for x in text.splitlines() if re.search(r'(?i)ERROR|failed|TsdOpen|not permitted|denied', x)]
        out.append({'file': str(p), 'bytes': p.stat().st_size,
                    'error_lines': [x[:700] for x in lines[:6]]})
print(json.dumps({'probe_logs': out}))
