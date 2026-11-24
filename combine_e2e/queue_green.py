# Copyright (c) 2026 TileXR Project.
"""Run the bounded queue diagnostic in a separate task-owned CPU container."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path

HOST=r'''
import hashlib,json,subprocess
from pathlib import Path
root=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
identity=json.loads((root/'identity.json').read_text())
image='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
assert identity['image']==image
tools=root/'queue-diagnostic-tools-a1'
tools.mkdir(exist_ok=False)
for name,text in request.items():
 assert name in ('queue_diagnostic.py','exports.py','check_local.py')
 (tools/name).write_text(text)
name='combine-ht-ea219fe0-qdiag-a1'
command=['docker','run','--rm','--name',name,'--runtime=runc','--network=none',
         '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=4','--memory=32g','--pids-limit=1024',
         '--shm-size=64m','--ulimit','nofile=524288:524288',
         '--label','crosszone.session=ea219fe0-2177-4850-84f0-c0da68a4a5fa',
         '--mount','type=bind,src='+str(root)+',dst=/work',
         image,'python3','/work/'+tools.name+'/queue_diagnostic.py','/work/'+tools.name]
try:
 with (root/'queue-diagnostic-container.log').open('wb') as log:
  p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=640)
finally:
 check=subprocess.run(['docker','inspect','--format','{{.Config.Labels}}',name],capture_output=True,timeout=10)
 if check.returncode==0 and b'crosszone.session:ea219fe0-2177-4850-84f0-c0da68a4a5fa' in check.stdout:
  subprocess.run(['docker','stop','--time','10',name],capture_output=True,timeout=25)
result={'diagnostic_only':True,'operator_qualified':False,'container_exit':p.returncode,'separate_container':True}
record=root/'cases/64p-t16-send-queue-stop40us-a1/diagnostic.json'
if record.is_file():result.update(json.loads(record.read_text()))
else:result['evidence']=str(root/'queue-diagnostic-container.log')
(root/'queue-diagnostic-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,separators=(',',':')))
raise SystemExit(p.returncode)
'''

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--args-file',type=Path,required=True)
    assert json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))=={}
    directory=Path(__file__).parent
    request={name:(directory/name).read_text(encoding='utf-8') for name in ('queue_diagnostic.py','exports.py','check_local.py')}
    source='request='+repr(request)+'\n'+HOST
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    p=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                      '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                      'timeout --kill-after=20 720 python3 -'],input=source.encode(),capture_output=True,timeout=760)
    (logs/'queue-diagnostic.stdout').write_bytes(p.stdout)
    (logs/'queue-diagnostic.stderr').write_bytes(p.stderr)
    if p.stdout.strip():print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else:print(json.dumps({'ssh_exit':p.returncode,'evidence':'green_local_logs'}))
    return p.returncode
if __name__=='__main__':raise SystemExit(main())
