# Copyright (c) 2026 TileXR Project.
"""Recover numerical evidence from retained SST dumps without executing kernels."""
import argparse,json,os,subprocess
from pathlib import Path

HOST=r'''
import hashlib,json,subprocess
from pathlib import Path
root=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
identity=json.loads((root/'identity.json').read_text())
cid=identity['container_id']
info=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
assert info['Id']==cid and info['Image']==identity['image'] and info['HostConfig']['NetworkMode']=='none'
state={k:info['State'].get(k) for k in ('Running','Status','ExitCode','OOMKilled','Error')}
case=root/'cases/64p-t4-local-send-a1'
run=json.loads((case/'run.json').read_text())
assert run['returncode']==0 and run['guards']['status']=='guards_verified'
assert 'Simulation is complete' in (case/'sim.log').read_text(errors='replace')
if not state['Running']:
 assert not state['OOMKilled'] and state['ExitCode']==0, 'container_termination_requires_diagnosis'
 old=cid
 p=subprocess.run(['docker','run','-d','--name','combine-ht-ea219fe0-013cc3e-stable-a1',
                   '--runtime=runc','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges',
                   '--cpus=8','--memory=32g','--pids-limit=1024','--shm-size=64m','--ulimit','nofile=524288:524288',
                   '--label','crosszone.session=ea219fe0-2177-4850-84f0-c0da68a4a5fa',
                   '--mount','type=bind,src='+str(root)+',dst=/work',
                   '--mount','type=bind,src='+str(root/'source')+',dst=/work/source,readonly',
                   identity['image'],'python3','-c','import signal; signal.pause()'],
                  check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25)
 cid=p.stdout.decode().strip()
 identity.update(previous_container_id=old,container_id=cid,container_name='combine-ht-ea219fe0-013cc3e-stable-a1')
 (root/'identity.json').write_text(json.dumps(identity,indent=2))
files=root/'export-recovery-tools'
files.mkdir(exist_ok=False)
(files/'exports.py').write_text(request['exports'])
(files/'check_local.py').write_text(request['checker'])
code="""
import importlib.util,json,subprocess
from pathlib import Path
case=Path('/work/cases/64p-t4-local-send-a1')
spec=importlib.util.spec_from_file_location('recovery_exports','/work/export-recovery-tools/exports.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
layout=json.loads((case/'export-layout.json').read_text())
env=json.loads((case/'sst-environment.json').read_text())
guards=module.verify_guards(layout,case/'bin',env['decoder_library'])
module.export_actual(layout,case/'bin',case/'actual-recovered',env['decoder_library'])
p=subprocess.run(['python3','/work/export-recovery-tools/check_local.py','check','--output',str(case/'fixture-data'),
                  '--actual',str(case/'actual-recovered')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
(case/'recovered-check.stdout').write_bytes(p.stdout)
(case/'recovered-check.stderr').write_bytes(p.stderr)
result={'qualified':p.returncode==0,'checker_exit':p.returncode,'guards':guards,'executed_sst':False}
if p.returncode==0: result['checked']=json.loads(p.stdout)
else:
 errors=[x for x in p.stderr.decode(errors='replace').splitlines() if x.startswith(('ValueError:','AssertionError:'))]
 result['first_error']=errors[-1][:300] if errors else 'see_green_check_log'
(case/'export-recovery.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,separators=(',',':')))
raise SystemExit(p.returncode)
"""
with (root/'export-recovery.log').open('wb') as log:
 p=subprocess.run(['docker','exec',cid,'python3','-c',code],stdout=log,stderr=subprocess.STDOUT,timeout=600)
result={'previous_container_state':state,'container_id':cid,'recovery_exit':p.returncode}
if (case/'export-recovery.json').is_file(): result.update(json.loads((case/'export-recovery.json').read_text()))
else: result['error']='see_export_recovery_log'
print(json.dumps(result,separators=(',',':')))
raise SystemExit(p.returncode)
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--args-file',type=Path,required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))=={}
    directory=Path(__file__).parent
    request={'exports':(directory/'exports.py').read_text(encoding='utf-8'),'checker':(directory/'check_local.py').read_text(encoding='utf-8')}
    code='request='+repr(request)+'\n'+HOST
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    p=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                      '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                      'timeout --kill-after=15 650 python3 -'],input=code.encode(),capture_output=True,timeout=680)
    (logs/'export-recovery.stdout').write_bytes(p.stdout)
    (logs/'export-recovery.stderr').write_bytes(p.stderr)
    if p.stdout.strip(): print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else: print(json.dumps({'ssh_exit':p.returncode,'evidence':'green_local_logs'}))
    return p.returncode

if __name__=='__main__': raise SystemExit(main())
