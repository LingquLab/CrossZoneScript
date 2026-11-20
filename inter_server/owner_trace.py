"""Read the original T4 BA traces without launching any simulator job."""
import json
from pathlib import Path
import re
import subprocess

source = Path(__file__).with_name('owner_trace_remote.py').read_bytes()
try:
    proc = subprocess.run(
        ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
         '-o', 'ConnectTimeout=10', 'root@141.61.94.58', 'python3', '-'],
        input=source, capture_output=True, timeout=90,
    )
    if proc.returncode == 0:
        result = json.loads(proc.stdout)
    else:
        diagnostic = proc.stderr.decode('utf-8', 'replace')
        classes = re.findall(r'^([A-Za-z]+(?:Error|Exception)):', diagnostic, re.MULTILINE)
        lines = re.findall(r'File "<stdin>", line (\d+)', diagnostic)
        result = {'observation': 'analysis_command_failed', 'ssh_exit_code': proc.returncode,
                  'error_classes': classes[-3:], 'remote_source_lines': lines[-4:]}
except (OSError, ValueError, subprocess.TimeoutExpired) as error:
    result = {'observation': 'transport_failed', 'error_type': type(error).__name__}
print(json.dumps(result, ensure_ascii=True))
