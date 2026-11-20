"""Stage a blue-prepared native case and bind existing SST platform resources.

No compiler, fixture prepare action, replacement operator or SST run is invoked.
"""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'alltoall'))
from ssh_transport import run

resources=Path(__file__).with_name('resources')
archive=resources/'native_t4.tar.gz'
metadata=json.loads((resources/'native_t4.tar.json').read_text())
import hashlib
assert hashlib.sha256(archive.read_bytes()).hexdigest()==metadata['archive_sha256']
host_root='/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54'
remote_archive=host_root+'/native-t4-'+metadata['archive_sha256'][:12]+'.tar.gz'
subprocess.run(['scp','-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(archive),
    'root@141.61.94.58:'+remote_archive],capture_output=True,check=True,timeout=180)
program='''import hashlib,json,subprocess,tarfile
from pathlib import Path,PurePosixPath
c=CONFIG
root=Path(HOST_ROOT)/c['case_relative']
archive=Path(ARCHIVE)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==c['archive_sha256']
root.mkdir(parents=True,exist_ok=False)
with tarfile.open(archive) as stream:
 members=stream.getmembers()
 assert len(members)<=4096 and sum(m.size for m in members)<=512*1024*1024
 for member in members:
  rel=PurePosixPath(member.name)
  assert member.isfile() and not rel.is_absolute() and '..' not in rel.parts
  target=root/member.name;target.parent.mkdir(parents=True,exist_ok=True)
  with target.open('xb') as out:out.write(stream.extractfile(member).read())
name='tilexr-cpu-b4f4660a-lr8'
info,=json.loads(subprocess.check_output(['docker','inspect',name],timeout=15))
assert info['Id']=='1f98e70b00ed8b171fea3b06152a7fe9507a2270e9dee05c72015af5b995c2f4'
assert not info['State']['Running'] and info['HostConfig']['Runtime']=='runc' and not info['HostConfig']['Devices']
subprocess.run(['docker','start',name],capture_output=True,check=True,timeout=30)
inner=INNER
try:
 p=subprocess.run(['docker','exec',name,'python3','-c',inner],capture_output=True,timeout=120)
 if p.returncode:
  (root/'prepare-error.log').write_bytes(p.stdout+p.stderr)
  raise RuntimeError('Native model preparation failed; inspect retained prepare-error.log')
 result=json.loads(p.stdout)
finally:subprocess.run(['docker','stop','--time','10',name],capture_output=True,check=True,timeout=25)
print(json.dumps(result,ensure_ascii=True))
'''
inner='''import hashlib,json,re,shutil
from pathlib import Path
import yaml
case=Path(CASE)
reference=Path('/work/lr8-source/lr8-64p-512b')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
identity=json.loads((case/'blue-case-identity.json').read_text())
for relative,expected in identity['blue_files'].items():assert sha(case/relative)==expected,relative
for directory in ('v310_config','UBusSim_config'):
 shutil.copytree(reference/directory,case/directory)
shutil.copyfile(case/'workload.yml',case/'v310_config/workload_config.yml')
for name in ('installed_model.py','environment.snapshot.json'):
 shutil.copyfile(reference/name,case/name)
snapshot=json.loads((case/'environment.snapshot.json').read_text())
for row in snapshot['runtime_files'].values():assert sha(Path(row['path']))==row['sha256'],row['path']
env={'sst_bin':snapshot['settings']['sst_bin'],'env_script':snapshot['settings']['sst_env_script'],
 'decoder_library':snapshot['settings']['library_dir']+'/libDavinciSimulator.so',
 'runtime_files':{row['path']:row['sha256'] for row in snapshot['runtime_files'].values()}}
for key in ('sst_bin','env_script','decoder_library'):env[key+'_sha256']=sha(Path(env[key]))
(case/'sst-environment.json').write_text(json.dumps(env,indent=2))
manifest={key:identity[key] for key in ('candidate_commit','kernel_sha256','entry','checker_sha256','fixture_files')}
manifest['files']={str(p.relative_to(case)):sha(p) for p in sorted(case.rglob('*')) if p.is_file()}
(case/'case-manifest.json').write_text(json.dumps(manifest,indent=2))
work=json.loads((case/'workload.yml').read_text())['workload_config']
assert len(work)==64 and all(row['block_dim']==16 and row['vec_func']==identity['entry'] for row in work.values())
result={'status':'native_case_prepared_not_run','candidate_commit':identity['candidate_commit'],
 'kernel_sha256':identity['kernel_sha256'],'case':str(case),'world':64,'block_dim':16,'model_block_evidence':[],'model_config':[]}
for path in sorted(Path('/opt/sim/simulator/v310_deployment').rglob('*.py')):
 if path.name=='component_param.py':continue
 text=path.read_text(errors='replace')
 lines=[{'line':i+1,'text':s.strip()[:500]} for i,s in enumerate(text.splitlines()) if re.search(r'block_dim|block_id|block_idx|aicore_num',s,re.I)]
 if lines:result['model_block_evidence'].append({'path':str(path),'sha256':sha(path),'matching_lines':len(lines),'lines':lines[:8]})
def collect(obj,path):
 if isinstance(obj,dict):
  for key,value in obj.items():collect(value,path+'/'+str(key))
 elif isinstance(obj,list):
  for i,value in enumerate(obj[:2]):collect(value,path+'/'+str(i))
 elif re.search(r'aicore_num|die_num|block_dim|subblock|block_id',path,re.I) and len(result['model_config'])<25:
  result['model_config'].append({'path':path,'value':obj})
collect(yaml.safe_load((case/'v310_config/davidV100_simulator_config.yml').read_text()),'david_config')
(case/'preparation-report.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,ensure_ascii=True))
'''
case='/work/'+metadata['case_relative']
inner='CASE='+repr(case)+'\n'+inner
program=('CONFIG='+repr(metadata)+'\nHOST_ROOT='+repr(host_root)+'\nARCHIVE='+repr(remote_archive)+
         '\nINNER='+repr(inner)+'\n'+program)
result=run(program,timeout=220)
report=Path('D:/project/inter-server-sst-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
(report/'native-t4-preparation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
