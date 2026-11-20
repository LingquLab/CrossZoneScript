"""Read bounded simulator asset locations on the authorized Linux host."""
import json
from pathlib import Path
import subprocess


def command(argv, timeout=20):
    p = subprocess.run(argv, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode('utf-8', errors='replace')


result = {}
container = 'sim-a2a-aarch64'
status, output = command(['docker', 'inspect', '--format', '{{json .State}}', container])
result['existing_container_running'] = json.loads(output).get('Running') if status == 0 else False
if result['existing_container_running']:
    search = '''from pathlib import Path
import json,os
matches=[]
for root in ['/work','/home/sim','/opt/sim']:
 if not Path(root).is_dir(): continue
 for base,dirs,files in os.walk(root):
  depth=len(Path(base).relative_to(root).parts)
  dirs[:]=[d for d in dirs if d not in ['Ascend','cann-9.1.0','local','node_modules','.git','input_bin','golden','dumps','OUTPUT'] and depth<5]
  for name in ['scenario.json','manifest.json','SOURCE_LOCK.json','environment.snapshot.json']:
   if name in files: matches.append(str(Path(base)/name))
  if len(matches)>=80: break
print(json.dumps(matches[:80]))
'''
    status, output = command(['docker', 'exec', container, 'timeout', '30', 'python3', '-c', search], 40)
    result['existing_asset_paths'] = json.loads(output) if status == 0 else []
result['host_candidate_directories'] = {}
for parent in ['/root', '/work', '/mnt/share', '/data', '/home']:
    path = Path(parent)
    if path.is_dir():
        result['host_candidate_directories'][parent] = sorted(
            str(p) for p in path.iterdir() if p.is_dir() and
            (parent in ['/home', '/mnt/share'] or any(t in p.name.lower() for t in ['tilexr', 'alltoall', 'repro', 'sst', 'sim']))
        )[:35]
print(json.dumps(result, ensure_ascii=True))
