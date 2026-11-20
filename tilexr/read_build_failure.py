"""Read existing phase evidence and process names without repeating any work."""
import json
import os
from pathlib import Path
import re
import subprocess


root = Path(os.environ['LOCALAPPDATA']) / 'CrossZone' / 'tilexr'
report = root / 'reports/lr8-1'
result = {'report': str(report), 'report_exists': report.is_dir()}
for name in ['progress.json', 'container.json', 'summary.json']:
    path = report / name
    if path.is_file():
        result[name] = json.loads(path.read_text(encoding='utf-8'))
result['phase_states'] = {}
for phase in ['source', 'build', 'abi', 'inspect']:
    path = report / phase / 'result.json'
    if path.is_file():
        result['phase_states'][phase] = json.loads(path.read_text(encoding='utf-8')).get('state')
result['diagnostic_lines'] = {}
for log in sorted(report.glob('*.stderr.log')):
    lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
    selected = [line for line in lines if len(line) <= 600 and re.search(
        r'(^[\w.]+(?:Error|Exception):|error:|Error response from daemon|No such file|not found|Permission denied|failed|timed out)', line)]
    if selected:
        result['diagnostic_lines'][log.name] = selected[-5:]
remote = '''import json,subprocess
from pathlib import Path
out={}
p=subprocess.run(['docker','inspect','--format','{{json .State}}','tilexr-matrix-acc8c509-lr8-1'],capture_output=True,timeout=15)
out['container_exists']=p.returncode==0
if p.returncode==0:
 state=json.loads(p.stdout); out['running']=state.get('Running'); out['exit_code']=state.get('ExitCode')
 p=subprocess.run(['docker','top','tilexr-matrix-acc8c509-lr8-1','-eo','pid,comm'],capture_output=True,timeout=15)
 out['process_names']=p.stdout.decode().splitlines()[:30] if p.returncode==0 else []
path=Path('/root/crosszone-tilexr-acc8c509/container.json')
if path.is_file(): out['created_identity']=json.loads(path.read_text())
print(json.dumps(out))
'''
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26', 'timeout 50 python3 -'],
    input=remote.encode(), capture_output=True, timeout=65)
if p.returncode:
    raise RuntimeError('Read-only remote state query failed')
result['remote_state'] = json.loads(p.stdout)
print(json.dumps(result, ensure_ascii=True))
