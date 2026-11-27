# Deploy identical scoped A/B packages without changing existing installations.
import json,subprocess,tempfile,shlex,hashlib
from pathlib import Path
keys=[p for p in Path('C:/Users/l00826879/.ssh').glob('*deploy*') if p.is_file() and p.suffix not in ['.pub','.old','.bak'] and p.stat().st_size<65536 and b'PRIVATE KEY' in p.read_bytes()]
assert keys
ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=NUL']
for key in keys:ssh+=['-i',str(key)]
root='/root/ubx-dispatch-global-ab-d23ffa56'
paths=['runtime-install','inputs']
for variant in ['control','global']:
 paths += [variant+'/source']+[variant+'/build/'+name for name in ['ubx_dispatch_ep32.o','ubx_dispatch_round_barrier.o','libubx_dispatch_host.so','ubx_dispatch_hardware_ep32']]
manifest_code='''import json,hashlib
from pathlib import Path
root=Path(ROOT);out={}
for name in PATHS:
 p=root/name
 assert p.exists(),name
 files=sorted(p.rglob('*')) if p.is_dir() else [p]
 for f in files:
  if f.is_symlink():out[str(f.relative_to(root))]={'symlink':str(f.readlink())}
  elif f.is_file():out[str(f.relative_to(root))]={'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
print(json.dumps(out))
'''.replace('ROOT',repr(root)).replace('PATHS',repr(paths))
def manifest(host):
 p=subprocess.run(ssh+['root@'+host,'python3 -'],input=manifest_code,text=True,capture_output=True,timeout=90)
 assert p.returncode==0
 return json.loads(p.stdout)
expected=manifest('90.90.93.81')
setup='from pathlib import Path; p=Path('+repr(root)+'); assert not p.exists(),"Destination exists; inspect before repeating deployment"; p.mkdir()'
p=subprocess.run(ssh+['root@90.90.93.83','python3 -'],input=setup,text=True,capture_output=True,timeout=20);assert p.returncode==0
with tempfile.TemporaryFile() as archive:
 p=subprocess.run(ssh+['root@90.90.93.81','tar -czf - -C '+shlex.quote(root)+' '+shlex.join(paths)],stdout=archive,stderr=subprocess.PIPE,timeout=180);assert p.returncode==0
 archive.seek(0)
 p=subprocess.run(ssh+['root@90.90.93.83','tar -xzf - -C '+shlex.quote(root)],stdin=archive,capture_output=True,timeout=180);assert p.returncode==0
assert manifest('90.90.93.83')==expected
print(json.dumps({'deployed':True,'source':'90.90.93.81','destination':'90.90.93.83','files':len(expected),'manifest_sha256':hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest(),'npu_launched':False}))
