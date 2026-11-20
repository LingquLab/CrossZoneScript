"""Read Docker CLI compatibility and the reserved simulation directory state."""
import json
import os
from pathlib import Path
import subprocess

remote = '''import json,subprocess,os
from pathlib import Path
def run(args):
 p=subprocess.run(args,capture_output=True,timeout=20)
 return p.returncode,p.stdout.decode(errors='replace')
r={}
code,out=run(['docker','version','--format','{{json .}}'])
if code==0:
 d=json.loads(out)
 r['client_version']=d.get('Client',{}).get('Version')
 r['server_version']=d.get('Server',{}).get('Version')
code,out=run(['docker','run','--help'])
r['supports_pull_option']='--pull' in out
code,out=run(['docker','info','--format','{{json .Runtimes}}'])
r['runtimes']=json.loads(out) if code==0 else None
code,out=run(['docker','inspect','--format','{{json .State}}','tilexr-matrix-acc8c509-lr8-1'])
r['container_exists']=code==0
work=Path('/root/crosszone-tilexr-acc8c509')
r['work_directory_exists']=work.is_dir()
r['work_directory_entries']=sorted(p.name for p in work.iterdir())[:20] if work.is_dir() else []
print(json.dumps(r))
'''
p = subprocess.run(['ssh', '-T', '-i', 'C:/Users/z00813871/.ssh/id_ed25519',
    '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ConnectTimeout=12', 'root@141.61.94.26', 'timeout 100 python3 -'],
    input=remote.encode(), capture_output=True, timeout=115)
if p.returncode:
    raise RuntimeError('Docker compatibility inspection failed')
result=json.loads(p.stdout)
root=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/reports/lr8-1'
(root/'docker-compatibility.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
