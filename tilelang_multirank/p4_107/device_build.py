from pathlib import Path
import hashlib,json,os,shlex,shutil,signal,subprocess,time
exp=Path('/root/tilelang-107-multirank-e28d87de-20261116-run01')
cann=Path('/usr/local/Ascend/cann-9.1.0')
revision='e28d87deb1e141e23ace5900594218ff47ab2525'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
assert os.geteuid()==0 and sha(Path('/etc/machine-id'))=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
assert shutil.disk_usage(exp).free>2147483648
prior=json.loads((exp/'source-and-core-build-summary.json').read_text())
assert prior['status']=='PASS' and prior['revision']==revision and prior['npu_launch'] is False
core=exp/'core-install';source=exp/'TileXR';module=source/'examples/tilelang_dispatch';backend=exp/'backend'
assert sha(core/'lib64/libtile-comm.so')=='836e28be984ccb4279b1b651a2aeb9f0754bbfb3d5eeb79152d0bdb4713eb6b7'
assert sha(backend/'src/tl_templates/ascend/common.h')=='d6355337873e5f7368c3ec7cb1f22ef0494663e133152120fee20265f03a1780'
prep=json.loads((exp/'preparation.json').read_text())
assert prep['cores']==[64]*4 and len(prep['abi_rows'])==16 and len(prep['cases'])==8 and len(prep['candidate_files'])==123
for name,digest in prep['candidate_files'].items():assert sha(source/name)==digest,name
for name,digest in prep['files'].items():assert sha(exp/name)==digest,name
builds=exp/'p4-builds';builds.mkdir(exist_ok=False)
phases=[];passed=True
def run(label,argv,limit):
 save(exp/(label+'.command.json'),[str(a) for a in argv]);started=time.monotonic();timed=False
 command='source '+shlex.quote(str(cann/'set_env.sh'))+' >/dev/null 2>&1 && export ASCEND_HOME_PATH='+shlex.quote(str(cann))+' && exec '+shlex.join([str(a) for a in argv])
 with (exp/(label+'.log')).open('xb') as out:
  p=subprocess.Popen(['bash','-c',command],cwd=source,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
  record={'pid':p.pid}
  try:record['start_ticks']=Path('/proc/'+str(p.pid)+'/stat').read_text().split(') ',1)[1].split()[19]
  except FileNotFoundError:record['already_exited']=True
  save(exp/(label+'.started.json'),record)
  try:code=p.wait(timeout=limit)
  except subprocess.TimeoutExpired:
   timed=True;os.killpg(p.pid,signal.SIGTERM)
   try:code=p.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);code=p.wait(timeout=5)
 row={'phase':label,'exit_code':code,'timed_out':timed,'seconds':round(time.monotonic()-started,3)};phases.append(row);save(exp/(label+'.receipt.json'),row)
 return code==0 and not timed
for tokens in [4,16,64,256]:
 name='p4-t'+str(tokens);v=builds/('t'+str(tokens));v.mkdir()
 args=['cmake','-S',module,'-B',v/'build','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0','-DCMAKE_INSTALL_LIBDIR=lib64','-DCMAKE_INSTALL_PREFIX='+str(v/'install'),'-DTL_CANN_ROOT='+str(cann),'-DTL_BACKEND_ROOT='+str(backend),'-DTL_COMM_PREFIX='+str(core),'-DTL_PRE_ALLREDUCE_SUPPORT=ON','-DTL_READY_PROBE=OFF','-DTL_DISPATCH_WORLD_SIZE=4','-DTL_DISPATCH_EXPERTS=8','-DTL_DISPATCH_CORES_PER_RANK=64;64;64;64','-DTL_TOKENS_PER_RANK='+str(tokens),'-DTL_GENERATED_ROOT='+str(exp/'generated'/('t'+str(tokens)))]
 if not run(name+'-configure',args,40):passed=False;break
 if not run(name+'-build',['cmake','--build',v/'build','-j4'],180):passed=False;break
 if not run(name+'-install',['cmake','--install',v/'build'],25):passed=False;break
 install=v/'install'
 assert len(list((install/'bin').glob('tilelang_dispatch_integrated_r*_profile?')))==8
 assert len(list((install/'lib64/tilexr/kernels').glob('tilelang_dispatch_r*_c64_profile?.o')))==8
 assert len(list((install/'lib64/tilexr/kernels').glob('*_rel.o')))==8
artifacts={str(p.relative_to(exp)):sha(p) for p in sorted(builds.rglob('*')) if p.is_file() and '/install/' in str(p)}
elf_types={}
for name in artifacts:
 p=exp/name;data=p.read_bytes()
 if name.endswith('.o'):
  assert data[:4]==b'\x7fELF' and data[5]==1
  kind=int.from_bytes(data[16:18],'little');assert kind==(1 if name.endswith('_rel.o') else 2),(name,kind)
  elf_types[name]=kind
 elif '/bin/' in name:
  assert data[:4]==b'\x7fELF' and int.from_bytes(data[18:20],'little')==183
  r=subprocess.run(['readelf','-d',str(p)],capture_output=True,timeout=10)
  assert r.returncode==0 and b'devlib' not in r.stdout
save(exp/'p4-artifact-hashes.json',artifacts);save(exp/'p4-elf-types.json',elf_types)
for name,digest in prep['candidate_files'].items():assert sha(source/name)==digest,name
for name,digest in prep['files'].items():assert sha(exp/name)==digest,name
assert sha(core/'lib64/libtile-comm.so')==prior['core_library_sha256']
result={'schema':'tilelang_107_p4_device_build.v1','target':'141.61.73.107','revision':revision,'status':'PASS' if passed else 'BUILD_FAILED','experiment_dir':str(exp),'phases':phases,'candidate_files_verified':123,'exports_verified':16,'fixtures_verified':8,'cores':[64]*4,'world_size':4,'experts':8,'tokens':[4,16,64,256],'host_core_cxx11_abi':0,'pre_allreduce_support':True,'ready_probe':False,'host_binaries':sum('/bin/' in s for s in artifacts),'linked_device_elfs':sum(s.endswith('.o') and not s.endswith('_rel.o') for s in artifacts),'relocatable_device_objects':sum(s.endswith('_rel.o') for s in artifacts),'artifact_manifest_sha256':sha(exp/'p4-artifact-hashes.json'),'core_library_sha256':prior['core_library_sha256'],'npu_launch':False,'runtime_validation':'NOT_RUN'}
if passed:assert (result['host_binaries'],result['linked_device_elfs'],result['relocatable_device_objects'])==(32,32,32)
else:
 lines=(exp/(phases[-1]['phase']+'.log')).read_text(errors='replace').splitlines();result['diagnostics']=[s[:200] for s in lines if any(t in s.lower() for t in ['error:','cmake error','undefined reference','fatal'])][:6]
save(exp/'p4-device-build-summary.json',result)
print(json.dumps(result,ensure_ascii=True,separators=(',',':')))
raise SystemExit(0 if passed else 1)
