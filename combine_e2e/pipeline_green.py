# Copyright (c) 2026 TileXR Project.
"""Compile the joint test entry in a separate, bounded CPU-only container."""
import argparse,json,os,subprocess
from pathlib import Path

HOST=r'''
import hashlib,json,subprocess
from pathlib import Path
root=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
identity=json.loads((root/'identity.json').read_text())
assert identity['revision']=='013cc3e2f8abf8157abdc609afcfe2369806b1e1'
assert identity['image']=='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
tools=root/'pipeline-build-tools-a1'
tools.mkdir(exist_ok=False)
for name,text in request.items():
 assert name in ('pipeline_kernel.cpp','pipeline_build.sh')
 (tools/name).write_text(text)
name='combine-ht-ea219fe0-joint-build-a1'
command=['docker','run','--rm','--name',name,'--runtime=runc','--network=none','--cap-drop=ALL',
         '--security-opt=no-new-privileges','--cpus=2','--memory=8g','--pids-limit=512',
         '--label','crosszone.session=ea219fe0-2177-4850-84f0-c0da68a4a5fa',
         '--mount','type=bind,src='+str(root)+',dst=/work',
         '--mount','type=bind,src='+str(root/'source')+',dst=/work/source,readonly',
         identity['image'],'bash','/work/'+tools.name+'/pipeline_build.sh','/work/'+tools.name+'/pipeline_kernel.cpp']
try:
 with (root/'pipeline-build-container.log').open('wb') as log:
  p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=280)
finally:
 check=subprocess.run(['docker','inspect','--format','{{.Config.Labels}}',name],capture_output=True,timeout=10)
 if check.returncode==0 and b'crosszone.session:ea219fe0-2177-4850-84f0-c0da68a4a5fa' in check.stdout:
  subprocess.run(['docker','stop','--time','10',name],capture_output=True,timeout=25)
result={'build_container_exit':p.returncode,'test_wrapper':True,'executed_sst':False,
        'wrapper_source_sha256':hashlib.sha256(request['pipeline_kernel.cpp'].encode()).hexdigest()}
report=root/'pipeline-build-013cc3e-a1/build-result.json'
if report.is_file():result.update(json.loads(report.read_text()))
else:result['evidence']=str(root/'pipeline-build-container.log')
(root/'pipeline-build-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,separators=(',',':')))
raise SystemExit(p.returncode)
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))=={'action':'build'}
    directory=Path(__file__).parent
    request={name:(directory/name).read_text(encoding='utf-8') for name in ('pipeline_kernel.cpp','pipeline_build.sh')}
    code='request='+repr(request)+'\n'+HOST
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    p=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                      '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                      'timeout --kill-after=20 340 python3 -'],input=code.encode(),capture_output=True,timeout=380)
    (logs/'pipeline-build.stdout').write_bytes(p.stdout)
    (logs/'pipeline-build.stderr').write_bytes(p.stderr)
    if p.stdout.strip():print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else:print(json.dumps({'ssh_exit':p.returncode,'evidence':'green_local_logs'}))
    return p.returncode
if __name__=='__main__':raise SystemExit(main())
