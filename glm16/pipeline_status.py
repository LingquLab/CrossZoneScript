"""Read this task's coordinator state and verify its exclusive lock is held."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
parser.parse_args()
source = r'''
import fcntl,json,pathlib,time
root=pathlib.Path('/home/glm16-09b20820');p=root/'pipeline-state.json'
out={'state_exists':p.is_file()}
if p.is_file():
 d=json.loads(p.read_text());out.update({k:d[k] for k in ['run_id','phase','log_directory','error_class','error'] if k in d})
 out['age_seconds']=round(time.time()-d['updated_at'],1)
 out['containers_created']=len(d.get('containers',{}))
 if 'download' in d:out['download']=d['download']
 lock=root/'pipeline.lock'
 if lock.is_file():
  with lock.open('r') as stream:
   try:fcntl.flock(stream.fileno(),fcntl.LOCK_SH|fcntl.LOCK_NB);out['lock_held']=False;fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
   except BlockingIOError:out['lock_held']=True
print(json.dumps(out))
'''
p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
    '-o', 'IdentitiesOnly=yes', '-i', 'C:/Users/l00929943/.ssh/id_ed25519_server',
    'root@141.61.94.30', 'python3', '-'], input=source, capture_output=True, text=True, timeout=30)
assert p.returncode == 0
print(json.dumps(json.loads(p.stdout)))
