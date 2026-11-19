import hashlib
import json
import platform
import re
from pathlib import Path

base = Path('/opt/sim/simulator/Testcases')
case = base / 'dispatch_memory_64p_top6_profile_bridge_20261119_run04'
diagnostic = base / 'dispatch_memory_64p_top6_profile_bridge_20261119_run05_debug'
log = case / 'run.log'
result = {'architecture': platform.machine(), 'result': 'READ_ONLY'}
if log.is_file() and log.stat().st_size < 8*1024*1024:
    data = log.read_bytes()
    lines = data.decode(errors='replace').splitlines()
    regex = re.compile(r'\b(fatal|exception|assert|segmentation|error|stall)\b|Simulation is complete|dumped core', re.I)
    result['run_log'] = {
        'sha256': hashlib.sha256(data).hexdigest(),
        'line_count': len(lines),
        'tail': [line[:180] for line in lines[-6:]],
        'markers': [[num, line[:200]] for num, line in enumerate(lines, 1) if regex.search(line)][-12:]
    }
compiler = diagnostic / 'compile-diagnostic.log'
if compiler.is_file() and compiler.stat().st_size < 128*1024:
    result['compiler_log'] = [line[:200] for line in compiler.read_text(errors='replace').splitlines()[-12:]]
print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
