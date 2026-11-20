"""Read the original T4 BA traces without launching any simulator job."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'alltoall'))
from ssh_transport import run

source = Path(__file__).with_name('owner_trace_remote.py').read_text(encoding='utf-8')
result = run(source, timeout=90)
print(json.dumps(result, ensure_ascii=True))
